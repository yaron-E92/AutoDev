from __future__ import annotations

from automation import queue_contract, queue_policy

import json
import os
import uuid
from pathlib import Path

from automation.claim_contract import (
    ClaimError,
    ClaimPolicy,
    DEFAULT_LEASE_MINUTES,
    DEFAULT_MAX_CONCURRENT_ISSUES,
    DEFAULT_MAX_NO_PROGRESS_ATTEMPTS,
    DEFAULT_MAX_NO_PROGRESS_MINUTES,
    MAX_CONCURRENT_ISSUES,
    MAX_LEASE_MINUTES,
    MAX_MAX_NO_PROGRESS_ATTEMPTS,
    MAX_MAX_NO_PROGRESS_MINUTES,
    MIN_LEASE_MINUTES,
    MIN_MAX_NO_PROGRESS_ATTEMPTS,
    MIN_MAX_NO_PROGRESS_MINUTES,
    WORKER_ID_ENV,
    WORKER_SCHEMA,
    WORKER_STATE,
    WorkerIdentity,
    _WORKER_ID,
)


def _validate_worker_id(value: str) -> str:
    worker_id = value.strip()
    if not _WORKER_ID.fullmatch(worker_id):
        raise ClaimError(
            "worker identity must be 1-64 characters using letters, digits, '.', '_' or '-', and start with a letter or digit"
        )
    return worker_id


def worker_state_path(*, home: Path | None = None) -> Path:
    return (home or Path.home()).expanduser().resolve() / WORKER_STATE


def set_worker_identity(worker_id: str, *, home: Path | None = None) -> WorkerIdentity:
    identity = WorkerIdentity(_validate_worker_id(worker_id))
    path = worker_state_path(home=home)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(identity.to_json(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
    return identity


def worker_identity(*, home: Path | None = None, create: bool = True) -> WorkerIdentity:
    override = os.environ.get(WORKER_ID_ENV, "").strip()
    if override:
        return WorkerIdentity(_validate_worker_id(override))
    path = worker_state_path(home=home)
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ClaimError(f"invalid AutoDev worker identity file: {path}") from exc
        if not isinstance(raw, dict) or raw.get("schema_version") != WORKER_SCHEMA:
            raise ClaimError(f"unsupported AutoDev worker identity schema: {path}")
        return WorkerIdentity(_validate_worker_id(str(raw.get("worker_id", ""))))
    if not create:
        raise ClaimError("AutoDev worker identity is not configured")
    generated = f"worker-{uuid.uuid4().hex[:12]}"
    return set_worker_identity(generated, home=home)


def _policy_int(
    raw: dict[str, object],
    key: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    value = raw.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ClaimError(f"queue policy {key} must be an integer")
    if not minimum <= value <= maximum:
        raise ClaimError(
            f"queue policy {key} must be between {minimum} and {maximum}"
        )
    return value


def load_claim_policy(repo: Path) -> ClaimPolicy:
    repo = repo.expanduser().resolve()
    # Keep the queue parser authoritative for core policy validity while allowing
    # the distributed-claim extension to remain backwards-compatible with v1 files.
    queue_policy.load_policy(repo)
    path = repo / queue_contract.QUEUE_CONFIG
    if not path.is_file():
        return ClaimPolicy()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ClaimError(f"invalid queue policy JSON: {path}") from exc
    if not isinstance(raw, dict):
        raise ClaimError(f"queue policy must be a JSON object: {path}")

    concurrency = _policy_int(
        raw,
        "max_concurrent_issues",
        DEFAULT_MAX_CONCURRENT_ISSUES,
        1,
        MAX_CONCURRENT_ISSUES,
    )
    lease = _policy_int(
        raw,
        "claim_lease_minutes",
        DEFAULT_LEASE_MINUTES,
        MIN_LEASE_MINUTES,
        MAX_LEASE_MINUTES,
    )
    no_progress_attempts = _policy_int(
        raw,
        "claim_max_no_progress_attempts",
        DEFAULT_MAX_NO_PROGRESS_ATTEMPTS,
        MIN_MAX_NO_PROGRESS_ATTEMPTS,
        MAX_MAX_NO_PROGRESS_ATTEMPTS,
    )
    no_progress_minutes = _policy_int(
        raw,
        "claim_max_no_progress_minutes",
        DEFAULT_MAX_NO_PROGRESS_MINUTES,
        MIN_MAX_NO_PROGRESS_MINUTES,
        MAX_MAX_NO_PROGRESS_MINUTES,
    )
    return ClaimPolicy(
        max_concurrent_issues=concurrency,
        lease_minutes=lease,
        max_no_progress_attempts=no_progress_attempts,
        max_no_progress_minutes=no_progress_minutes,
    )
