from __future__ import annotations

import json
import re
import subprocess
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, TextIO

from automation.claim_contract import (
    CLAIM_LIVENESS_ACTIVE,
    CLAIM_LIVENESS_STALLED,
    CLAIM_MESSAGE,
    CLAIM_REF_PREFIX,
    CLAIM_SCHEMA,
    Claim,
    ClaimError,
    _iso,
    _now,
    _parse_time,
    claim_ref,
)
from automation.claim_identity import (
    _validate_worker_id,
)
from automation.claim_process import (
    _git,
    _is_push_race,
    _require_ok,
    _returncode,
    _stdout,
)


def _remote_ref_sha(
    repo: Path,
    ref: str,
    *,
    runner: Callable[..., object] = subprocess.run,
) -> str:
    result = _git(repo, ["ls-remote", "--heads", "origin", ref], runner=runner)
    lines = [line.strip() for line in _stdout(result).splitlines() if line.strip()]
    for line in lines:
        fields = line.split()
        if len(fields) >= 2 and fields[1] == ref:
            return fields[0]
    return ""


def _claim_message(metadata: dict[str, object]) -> str:
    return CLAIM_MESSAGE + "\n" + json.dumps(metadata, sort_keys=True, separators=(",", ":")) + "\n"


def _parse_claim_message(message: str, *, ref: str, sha: str) -> Claim:
    lines = message.splitlines()
    if not lines or lines[0].strip() != CLAIM_MESSAGE:
        raise ClaimError(f"remote AutoDev claim ref contains unrecognized metadata: {ref}")
    payload_text = "\n".join(lines[1:]).strip()
    try:
        raw = json.loads(payload_text)
    except json.JSONDecodeError as exc:
        raise ClaimError(f"remote AutoDev claim metadata is invalid JSON: {ref}") from exc
    if not isinstance(raw, dict) or raw.get("schema_version") != CLAIM_SCHEMA:
        raise ClaimError(f"unsupported AutoDev claim schema on {ref}")

    progress_id = str(raw.get("progress_id", "") or "")
    progress_at = str(raw.get("progress_at", "") or "")
    progress_summary = str(raw.get("progress_summary", "") or "")[:240]
    no_progress_attempts = int(raw.get("no_progress_attempts", 0) or 0)
    liveness_state = str(
        raw.get("liveness_state", CLAIM_LIVENESS_ACTIVE) or CLAIM_LIVENESS_ACTIVE
    ).casefold()

    claim = Claim(
        repository=str(raw.get("repository", "")),
        issue_number=int(raw.get("issue_number", 0) or 0),
        worker_id=_validate_worker_id(str(raw.get("worker_id", ""))),
        run_id=str(raw.get("run_id", "")),
        claim_id=str(raw.get("claim_id", "")),
        acquired_at=str(raw.get("acquired_at", "")),
        heartbeat_at=str(raw.get("heartbeat_at", "")),
        lease_seconds=int(raw.get("lease_seconds", 0) or 0),
        ref=ref,
        sha=sha,
        progress_id=progress_id,
        progress_at=progress_at,
        progress_summary=progress_summary,
        no_progress_attempts=no_progress_attempts,
        liveness_state=liveness_state,
    )
    if claim.issue_number <= 0 or claim.ref != claim_ref(claim.issue_number):
        raise ClaimError(f"claim issue/ref identity mismatch on {ref}")
    if not claim.repository or not claim.run_id or not claim.claim_id:
        raise ClaimError(f"claim metadata is incomplete on {ref}")
    if claim.lease_seconds <= 0:
        raise ClaimError(f"claim lease is invalid on {ref}")
    if claim.progress_id and not re.fullmatch(r"[0-9a-f]{64}", claim.progress_id):
        raise ClaimError(f"claim durable progress identity is invalid on {ref}")
    if claim.no_progress_attempts < 0:
        raise ClaimError(f"claim no-progress attempt count is invalid on {ref}")
    if claim.liveness_state not in {CLAIM_LIVENESS_ACTIVE, CLAIM_LIVENESS_STALLED}:
        raise ClaimError(f"claim liveness state is invalid on {ref}")
    _parse_time(claim.acquired_at)
    _parse_time(claim.heartbeat_at)
    if claim.progress_at:
        _parse_time(claim.progress_at)
    return claim


def _read_claim_from_ref(
    repo: Path,
    ref: str,
    sha: str,
    *,
    runner: Callable[..., object] = subprocess.run,
) -> Claim:
    _git(repo, ["fetch", "--quiet", "--no-tags", "origin", ref], runner=runner)
    shown = _git(repo, ["show", "-s", "--format=%B", sha], runner=runner)
    return _parse_claim_message(_stdout(shown), ref=ref, sha=sha)


def get_claim(
    repo: Path,
    issue_number: int,
    *,
    runner: Callable[..., object] = subprocess.run,
) -> Claim | None:
    ref = claim_ref(issue_number)
    sha = _remote_ref_sha(repo, ref, runner=runner)
    if not sha:
        return None
    return _read_claim_from_ref(repo, ref, sha, runner=runner)


def list_claims(
    repo: Path,
    *,
    runner: Callable[..., object] = subprocess.run,
) -> tuple[Claim, ...]:
    pattern = CLAIM_REF_PREFIX + "*"
    result = _git(repo, ["ls-remote", "--heads", "origin", pattern], runner=runner)
    pairs: list[tuple[str, str]] = []
    for line in _stdout(result).splitlines():
        fields = line.split()
        if len(fields) < 2 or not fields[1].startswith(CLAIM_REF_PREFIX):
            continue
        pairs.append((fields[1], fields[0]))
    claims = [
        _read_claim_from_ref(repo, ref, sha, runner=runner)
        for ref, sha in sorted(pairs)
    ]
    return tuple(sorted(claims, key=lambda item: item.issue_number))


def claim_expired(claim: Claim, *, now: datetime | None = None) -> bool:
    current = (now or _now()).astimezone(timezone.utc)
    heartbeat = _parse_time(claim.heartbeat_at)
    return current >= heartbeat + timedelta(seconds=claim.lease_seconds)


def _base_commit(repo: Path, base_ref: str, *, runner: Callable[..., object]) -> str:
    result = _git(repo, ["rev-parse", "--verify", base_ref], runner=runner)
    value = _stdout(result).strip()
    if not re.fullmatch(r"[0-9a-fA-F]{40,64}", value):
        raise ClaimError(f"could not resolve claim base ref: {base_ref}")
    return value


def _create_claim_commit(
    repo: Path,
    parent_sha: str,
    metadata: dict[str, object],
    *,
    runner: Callable[..., object],
) -> str:
    tree_result = _git(repo, ["rev-parse", f"{parent_sha}^{{tree}}"], runner=runner)
    tree = _stdout(tree_result).strip()
    args = [
        "-c",
        "user.name=AutoDev Claim",
        "-c",
        "user.email=autodev-claim@localhost",
        "commit-tree",
        tree,
        "-p",
        parent_sha,
    ]
    result = _git(
        repo,
        args,
        runner=runner,
        input_text=_claim_message(metadata),
    )
    sha = _stdout(result).strip()
    if not re.fullmatch(r"[0-9a-fA-F]{40,64}", sha):
        raise ClaimError("git commit-tree did not return a claim commit SHA")
    return sha


def _stable_claim_parent(
    repo: Path,
    claim: Claim,
    *,
    runner: Callable[..., object],
) -> str:
    """Return the stable non-heartbeat parent for this claim.

    Older AutoDev versions chained each heartbeat commit onto the previous
    heartbeat. Walk only through commits that parse as the same claim identity;
    the first different/non-claim parent is the repository commit on which the
    claim was originally rooted.
    """
    current_sha = claim.sha
    while True:
        parents_result = _git(repo, ["show", "-s", "--format=%P", current_sha], runner=runner)
        parents = _stdout(parents_result).split()
        if len(parents) != 1:
            raise ClaimError(
                f"claim commit must have exactly one parent: {claim.ref} at {current_sha}"
            )
        parent_sha = parents[0]
        message_result = _git(repo, ["show", "-s", "--format=%B", parent_sha], runner=runner)
        try:
            parent_claim = _parse_claim_message(
                _stdout(message_result),
                ref=claim.ref,
                sha=parent_sha,
            )
        except ClaimError:
            return parent_sha

        same_identity = (
            parent_claim.repository == claim.repository
            and parent_claim.issue_number == claim.issue_number
            and parent_claim.worker_id == claim.worker_id
            and parent_claim.run_id == claim.run_id
            and parent_claim.claim_id == claim.claim_id
            and parent_claim.acquired_at == claim.acquired_at
            and parent_claim.lease_seconds == claim.lease_seconds
        )
        if not same_identity:
            return parent_sha
        current_sha = parent_sha


def _claim_metadata(
    *,
    github_repo: str,
    issue_number: int,
    worker_id: str,
    run_id: str,
    claim_id: str,
    acquired_at: str,
    heartbeat_at: str,
    lease_seconds: int,
    progress_id: str = "",
    progress_at: str = "",
    progress_summary: str = "",
    no_progress_attempts: int = 0,
    liveness_state: str = CLAIM_LIVENESS_ACTIVE,
) -> dict[str, object]:
    return {
        "schema_version": CLAIM_SCHEMA,
        "repository": github_repo,
        "issue_number": issue_number,
        "worker_id": worker_id,
        "run_id": run_id,
        "claim_id": claim_id,
        "acquired_at": acquired_at,
        "heartbeat_at": heartbeat_at,
        "lease_seconds": lease_seconds,
        "progress_id": progress_id,
        "progress_at": progress_at,
        "progress_summary": progress_summary[:240],
        "no_progress_attempts": no_progress_attempts,
        "liveness_state": liveness_state,
    }


def _push_with_lease(
    repo: Path,
    *,
    ref: str,
    new_sha: str,
    expected_sha: str,
    runner: Callable[..., object],
) -> bool:
    expected = expected_sha
    lease = f"--force-with-lease={ref}:{expected}"
    result = _git(
        repo,
        ["push", lease, "origin", f"{new_sha}:{ref}"],
        runner=runner,
        check=False,
    )
    if _returncode(result) == 0:
        return True
    if _is_push_race(result):
        return False
    _require_ok(result, ["git", "push", lease, "origin", f"{new_sha}:{ref}"])
    return False


def _delete_with_lease(
    repo: Path,
    claim: Claim,
    *,
    runner: Callable[..., object],
) -> bool:
    lease = f"--force-with-lease={claim.ref}:{claim.sha}"
    result = _git(
        repo,
        ["push", lease, "origin", f":{claim.ref}"],
        runner=runner,
        check=False,
    )
    if _returncode(result) == 0:
        return True
    if _is_push_race(result):
        return False
    _require_ok(result, ["git", "push", lease, "origin", f":{claim.ref}"])
    return False


def _new_claim(
    repo: Path,
    github_repo: str,
    issue_number: int,
    worker_id: str,
    base_ref: str,
    *,
    lease_minutes: int,
    runner: Callable[..., object],
    now: datetime,
    progress_id: str = "",
    progress_at: str = "",
    progress_summary: str = "",
) -> Claim:
    ref = claim_ref(issue_number)
    acquired = _iso(now)
    run_id = uuid.uuid4().hex
    claim_id = uuid.uuid4().hex
    metadata = _claim_metadata(
        github_repo=github_repo,
        issue_number=issue_number,
        worker_id=worker_id,
        run_id=run_id,
        claim_id=claim_id,
        acquired_at=acquired,
        heartbeat_at=acquired,
        lease_seconds=lease_minutes * 60,
        progress_id=progress_id,
        progress_at=progress_at or acquired,
        progress_summary=progress_summary,
        no_progress_attempts=0,
        liveness_state=CLAIM_LIVENESS_ACTIVE,
    )
    parent = _base_commit(repo, base_ref, runner=runner)
    sha = _create_claim_commit(repo, parent, metadata, runner=runner)
    return Claim(
        repository=github_repo,
        issue_number=issue_number,
        worker_id=worker_id,
        run_id=run_id,
        claim_id=claim_id,
        acquired_at=acquired,
        heartbeat_at=acquired,
        lease_seconds=lease_minutes * 60,
        ref=ref,
        sha=sha,
        progress_id=progress_id,
        progress_at=progress_at or acquired,
        progress_summary=progress_summary[:240],
        no_progress_attempts=0,
        liveness_state=CLAIM_LIVENESS_ACTIVE,
    )
