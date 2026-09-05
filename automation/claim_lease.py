from __future__ import annotations

import subprocess
import threading
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, TextIO

from automation.claim_contract import (
    Claim,
    ClaimAttempt,
    ClaimError,
    ClaimPolicy,
    _iso,
    _now,
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
) -> ClaimAttempt:
    repo = repo.expanduser().resolve()
    worker_id = _validate_worker_id(worker_id)
    current = (now or _now()).astimezone(timezone.utc)
    claim_policy = policy or load_claim_policy(repo)
    existing = get_claim(repo, issue_number, runner=runner)
    if existing is not None:
        if existing.repository != github_repo:
            raise ClaimError(
                f"claim repository identity mismatch on {existing.ref}: {existing.repository!r}"
            )
        if not claim_expired(existing, now=current):
            if existing.worker_id == worker_id:
                renewed = renew_claim(repo, existing, runner=runner, now=current)
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
) -> Claim | None:
    current = (now or _now()).astimezone(timezone.utc)
    metadata = _claim_metadata(
        github_repo=claim.repository,
        issue_number=claim.issue_number,
        worker_id=claim.worker_id,
        run_id=claim.run_id,
        claim_id=claim.claim_id,
        acquired_at=claim.acquired_at,
        heartbeat_at=_iso(current),
        lease_seconds=claim.lease_seconds,
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
    return replace(claim, heartbeat_at=_iso(current), sha=sha)


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
