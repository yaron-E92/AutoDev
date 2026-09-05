from __future__ import annotations

import subprocess
import threading
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from automation import claim_liveness
from automation.claim_contract import (
    CLAIM_LIVENESS_ACTIVE,
    CLAIM_LIVENESS_STALLED,
    Claim,
    ClaimAttempt,
    ClaimError,
    ClaimPolicy,
    _iso,
    _now,
    _parse_time,
)
from automation.claim_identity import (
    _validate_worker_id,
    load_claim_policy,
)
from automation.claim_recovery import (
    recovery_evidence,
)
from automation.claim_repository import (
    _claim_metadata,
    _create_claim_commit,
    _delete_with_lease,
    _new_claim,
    _push_with_lease,
    _stable_claim_parent,
    claim_expired,
    get_claim,
    list_claims,
)


def _replace_claim_state(
    repo: Path,
    claim: Claim,
    *,
    heartbeat_at: str,
    progress_id: str,
    progress_at: str,
    progress_summary: str,
    no_progress_attempts: int,
    liveness_state: str,
    runner: Callable[..., object],
) -> Claim | None:
    metadata = _claim_metadata(
        github_repo=claim.repository,
        issue_number=claim.issue_number,
        worker_id=claim.worker_id,
        run_id=claim.run_id,
        claim_id=claim.claim_id,
        acquired_at=claim.acquired_at,
        heartbeat_at=heartbeat_at,
        lease_seconds=claim.lease_seconds,
        progress_id=progress_id,
        progress_at=progress_at,
        progress_summary=progress_summary,
        no_progress_attempts=no_progress_attempts,
        liveness_state=liveness_state,
    )
    parent_sha = _stable_claim_parent(repo, claim, runner=runner)
    sha = _create_claim_commit(repo, parent_sha, metadata, runner=runner)
    if not _push_with_lease(
        repo,
        ref=claim.ref,
        new_sha=sha,
        expected_sha=claim.sha,
        runner=runner,
    ):
        return None
    return replace(
        claim,
        heartbeat_at=heartbeat_at,
        sha=sha,
        progress_id=progress_id,
        progress_at=progress_at,
        progress_summary=progress_summary[:240],
        no_progress_attempts=no_progress_attempts,
        liveness_state=liveness_state,
    )


def _stalled_detail(
    claim: Claim,
    snapshot: claim_liveness.ProgressSnapshot,
    *,
    policy: ClaimPolicy,
) -> str:
    progress_at = claim.progress_at or claim.acquired_at
    return (
        f"RUN_STALLED issue #{claim.issue_number}; "
        f"claim owner={claim.worker_id}; claim={claim.claim_id}; run={claim.run_id}; "
        f"last durable progress={progress_at}; "
        f"no-progress attempts={claim.no_progress_attempts}/"
        f"{policy.max_no_progress_attempts}; "
        f"progress={claim.progress_id[:12] or 'legacy'}; "
        f"{snapshot.summary}; "
        f"recovery: inspect the dedicated worker and run `autodev resume` there "
        f"manually while the stalled claim continues to protect the issue; once "
        f"durable progress changes, the owning worker may renew it. Do not delete "
        f"the remote claim ref by hand."
    )


def _stalled_protected_detail(claim: Claim) -> str:
    return (
        f"issue #{claim.issue_number} has a stalled distributed claim owned by "
        f"{claim.worker_id}; claim={claim.claim_id}; run={claim.run_id}; "
        f"last durable progress={claim.progress_at or claim.acquired_at}; "
        f"no-progress attempts={claim.no_progress_attempts}; "
        f"progress={claim.progress_id[:12] or 'legacy'}; "
        f"the claim remains protected until the owning run advances or reaches a "
        f"terminal state through supported AutoDev recovery"
    )


def _progress_snapshot(
    repo: Path,
    issue_number: int,
    *,
    runner: Callable[..., object],
    progress_inspector: Callable[[Path, int], claim_liveness.ProgressSnapshot] | None,
) -> claim_liveness.ProgressSnapshot:
    if progress_inspector is not None:
        return progress_inspector(repo, issue_number)
    return claim_liveness.progress_snapshot(repo, issue_number, runner=runner)


def _renew_for_scheduler_tick(
    repo: Path,
    claim: Claim,
    snapshot: claim_liveness.ProgressSnapshot,
    *,
    policy: ClaimPolicy,
    runner: Callable[..., object],
    now: datetime,
) -> Claim | None:
    now_text = _iso(now)
    if not claim.progress_id or claim.progress_id != snapshot.identity:
        return _replace_claim_state(
            repo,
            claim,
            heartbeat_at=now_text,
            progress_id=snapshot.identity,
            progress_at=now_text,
            progress_summary=snapshot.summary,
            no_progress_attempts=0,
            liveness_state=CLAIM_LIVENESS_ACTIVE,
            runner=runner,
        )

    progress_at = claim.progress_at or claim.acquired_at
    elapsed = now - _parse_time(progress_at)
    attempts = claim.no_progress_attempts + 1
    elapsed_minutes = max(0.0, elapsed.total_seconds() / 60.0)
    if (
        attempts >= policy.max_no_progress_attempts
        or elapsed_minutes >= policy.max_no_progress_minutes
    ):
        stalled = _replace_claim_state(
            repo,
            claim,
            heartbeat_at=claim.heartbeat_at,
            progress_id=snapshot.identity,
            progress_at=progress_at,
            progress_summary=snapshot.summary,
            no_progress_attempts=attempts,
            liveness_state=CLAIM_LIVENESS_STALLED,
            runner=runner,
        )
        if stalled is None:
            return None
        raise ClaimError(_stalled_detail(stalled, snapshot, policy=policy))

    return _replace_claim_state(
        repo,
        claim,
        heartbeat_at=now_text,
        progress_id=snapshot.identity,
        progress_at=progress_at,
        progress_summary=snapshot.summary,
        no_progress_attempts=attempts,
        liveness_state=CLAIM_LIVENESS_ACTIVE,
        runner=runner,
    )


def acquire_claim(
    repo: Path,
    github_repo: str,
    issue_number: int,
    worker_id: str,
    base_ref: str,
    *,
    policy: ClaimPolicy | None = None,
    runner: Callable[..., object] = subprocess.run,
    now: datetime | None = None,
    evidence_checker: Callable[[Path, str, int], tuple[str, ...]] | None = None,
    progress_inspector: Callable[[Path, int], claim_liveness.ProgressSnapshot] | None = None,
) -> ClaimAttempt:
    repo = repo.expanduser().resolve()
    worker_id = _validate_worker_id(worker_id)
    current = (now or _now()).astimezone(timezone.utc)
    claim_policy = policy or load_claim_policy(repo)
    snapshot = _progress_snapshot(
        repo,
        issue_number,
        runner=runner,
        progress_inspector=progress_inspector,
    )
    existing = get_claim(repo, issue_number, runner=runner)
    if existing is not None:
        if existing.repository != github_repo:
            raise ClaimError(
                f"claim repository identity mismatch on {existing.ref}: {existing.repository!r}"
            )

        if existing.liveness_state == CLAIM_LIVENESS_STALLED:
            if existing.worker_id != worker_id:
                return ClaimAttempt(
                    "STALE_PROTECTED",
                    owner=existing,
                    detail=_stalled_protected_detail(existing),
                )
            if existing.progress_id != snapshot.identity:
                renewed = _replace_claim_state(
                    repo,
                    existing,
                    heartbeat_at=_iso(current),
                    progress_id=snapshot.identity,
                    progress_at=_iso(current),
                    progress_summary=snapshot.summary,
                    no_progress_attempts=0,
                    liveness_state=CLAIM_LIVENESS_ACTIVE,
                    runner=runner,
                )
                if renewed is not None:
                    return ClaimAttempt("OWNED", claim=renewed, owner=renewed)
                winner = get_claim(repo, issue_number, runner=runner)
                return ClaimAttempt(
                    "BUSY",
                    owner=winner,
                    detail="stalled-claim recovery race was won by another worker",
                )
            raise ClaimError(_stalled_detail(existing, snapshot, policy=claim_policy))

        if not claim_expired(existing, now=current):
            if existing.worker_id == worker_id:
                renewed = _renew_for_scheduler_tick(
                    repo,
                    existing,
                    snapshot,
                    policy=claim_policy,
                    runner=runner,
                    now=current,
                )
                if renewed is not None:
                    return ClaimAttempt("OWNED", claim=renewed, owner=renewed)
            return ClaimAttempt(
                "BUSY",
                owner=existing,
                detail=f"issue #{issue_number} is actively claimed by {existing.worker_id}",
            )
        checker = evidence_checker or (
            lambda path, repository, issue: recovery_evidence(
                path,
                repository,
                issue,
                runner=runner,
            )
        )
        evidence = checker(repo, github_repo, issue_number)
        if evidence:
            return ClaimAttempt(
                "STALE_PROTECTED",
                owner=existing,
                detail="; ".join(evidence),
            )
        if not _delete_with_lease(repo, existing, runner=runner):
            winner = get_claim(repo, issue_number, runner=runner)
            return ClaimAttempt(
                "BUSY",
                owner=winner,
                detail="stale-claim recovery race was won by another worker",
            )

    candidate = _new_claim(
        repo,
        github_repo,
        issue_number,
        worker_id,
        base_ref,
        lease_minutes=claim_policy.lease_minutes,
        runner=runner,
        now=current,
        progress_id=snapshot.identity,
        progress_at=_iso(current),
        progress_summary=snapshot.summary,
    )
    if _push_with_lease(
        repo,
        ref=candidate.ref,
        new_sha=candidate.sha,
        expected_sha="",
        runner=runner,
    ):
        return ClaimAttempt("ACQUIRED", claim=candidate, owner=candidate)
    winner = get_claim(repo, issue_number, runner=runner)
    return ClaimAttempt(
        "BUSY",
        owner=winner,
        detail="distributed claim race was won by another worker",
    )


def renew_claim(
    repo: Path,
    claim: Claim,
    *,
    runner: Callable[..., object] = subprocess.run,
    now: datetime | None = None,
    progress_id: str | None = None,
    progress_at: str | None = None,
    progress_summary: str | None = None,
    no_progress_attempts: int | None = None,
    liveness_state: str | None = None,
) -> Claim | None:
    target_state = liveness_state or claim.liveness_state
    if claim.liveness_state == CLAIM_LIVENESS_STALLED and target_state == CLAIM_LIVENESS_STALLED:
        raise ClaimError(
            f"refusing to extend heartbeat for stalled claim on issue #{claim.issue_number}; "
            "advance/recover the durable run first"
        )
    current = (now or _now()).astimezone(timezone.utc)
    return _replace_claim_state(
        repo,
        claim,
        heartbeat_at=_iso(current),
        progress_id=claim.progress_id if progress_id is None else progress_id,
        progress_at=claim.progress_at if progress_at is None else progress_at,
        progress_summary=(
            claim.progress_summary if progress_summary is None else progress_summary
        ),
        no_progress_attempts=(
            claim.no_progress_attempts
            if no_progress_attempts is None
            else no_progress_attempts
        ),
        liveness_state=target_state,
        runner=runner,
    )


def release_claim(
    repo: Path,
    claim: Claim,
    *,
    runner: Callable[..., object] = subprocess.run,
) -> bool:
    current = get_claim(repo, claim.issue_number, runner=runner)
    if current is None:
        return True
    if current.worker_id != claim.worker_id or current.claim_id != claim.claim_id:
        return False
    return _delete_with_lease(repo, current, runner=runner)


def active_claims(
    repo: Path,
    *,
    runner: Callable[..., object] = subprocess.run,
    now: datetime | None = None,
    include_stale: bool = False,
) -> tuple[Claim, ...]:
    claims = list_claims(repo, runner=runner)
    if include_stale:
        return claims
    return tuple(item for item in claims if not claim_expired(item, now=now))


class HeartbeatLease:
    def __init__(
        self,
        repo: Path,
        claim: Claim,
        *,
        runner: Callable[..., object] = subprocess.run,
        interval_seconds: float | None = None,
    ) -> None:
        self.repo = repo.expanduser().resolve()
        self.claim = claim
        self.runner = runner
        self.interval_seconds = interval_seconds or max(
            30.0,
            min(300.0, claim.lease_seconds / 3.0),
        )
        self.lost = False
        self.error = ""
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._mutex = threading.Lock()

    def _loop(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            try:
                with self._mutex:
                    renewed = renew_claim(
                        self.repo,
                        self.claim,
                        runner=self.runner,
                    )
                    if renewed is None:
                        self.lost = True
                        self.error = "distributed claim ownership changed while the run was active"
                        self._stop.set()
                        return
                    self.claim = renewed
            except Exception as exc:
                # Do not immediately declare ownership lost for a transient network
                # failure. The lease timestamp remains authoritative; stale recovery
                # cannot take over until the published heartbeat actually expires.
                self.error = str(exc)

    def __enter__(self) -> "HeartbeatLease":
        self._thread = threading.Thread(
            target=self._loop,
            name=f"autodev-claim-{self.claim.issue_number}",
            daemon=True,
        )
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(1.0, min(5.0, self.interval_seconds)))
            self._thread = None

    def latest_claim(self) -> Claim:
        with self._mutex:
            return self.claim
