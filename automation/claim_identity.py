from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from automation import issue_queue

from automation.claim_contract import (
    ClaimError,
    ClaimPolicy,
    DEFAULT_LEASE_MINUTES,
    DEFAULT_MAX_CONCURRENT_ISSUES,
    MAX_CONCURRENT_ISSUES,
    MAX_LEASE_MINUTES,
    MIN_LEASE_MINUTES,
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

def load_claim_policy(repo: Path) -> ClaimPolicy:
    repo = repo.expanduser().resolve()
    # Keep the queue parser authoritative for core policy validity while allowing
    # the distributed-claim extension to remain backwards-compatible with v1 files.
    issue_queue.load_policy(repo)
    path = repo / issue_queue.QUEUE_CONFIG
    if not path.is_file():
        return ClaimPolicy()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ClaimError(f"invalid queue policy JSON: {path}") from exc
    if not isinstance(raw, dict):
        raise ClaimError(f"queue policy must be a JSON object: {path}")
    concurrency = raw.get("max_concurrent_issues", DEFAULT_MAX_CONCURRENT_ISSUES)
    lease = raw.get("claim_lease_minutes", DEFAULT_LEASE_MINUTES)
    if not isinstance(concurrency, int) or isinstance(concurrency, bool):
        raise ClaimError("queue policy max_concurrent_issues must be an integer")
    if not 1 <= concurrency <= MAX_CONCURRENT_ISSUES:
        raise ClaimError(
            f"queue policy max_concurrent_issues must be between 1 and {MAX_CONCURRENT_ISSUES}"
        )
    if not isinstance(lease, int) or isinstance(lease, bool):
        raise ClaimError("queue policy claim_lease_minutes must be an integer")
    if not MIN_LEASE_MINUTES <= lease <= MAX_LEASE_MINUTES:
        raise ClaimError(
            f"queue policy claim_lease_minutes must be between {MIN_LEASE_MINUTES} and {MAX_LEASE_MINUTES}"
        )
    return ClaimPolicy(max_concurrent_issues=concurrency, lease_minutes=lease)
