from __future__ import annotations

import json
import re
import subprocess
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, TextIO

from automation.claim_contract import (
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
    )
    if claim.issue_number <= 0 or claim.ref != claim_ref(claim.issue_number):
        raise ClaimError(f"claim issue/ref identity mismatch on {ref}")
    if not claim.repository or not claim.run_id or not claim.claim_id:
        raise ClaimError(f"claim metadata is incomplete on {ref}")
    if claim.lease_seconds <= 0:
        raise ClaimError(f"claim lease is invalid on {ref}")
    _parse_time(claim.acquired_at)
    _parse_time(claim.heartbeat_at)
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
    )
