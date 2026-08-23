from __future__ import annotations

import re
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path


CLAIM_SCHEMA = 1

WORKER_SCHEMA = 1

CLAIM_MESSAGE = "AUTODEV_DISTRIBUTED_CLAIM_V1"

CLAIM_REF_PREFIX = "refs/heads/autodev/claims/issue-"

WORKER_STATE = Path(".autodev") / "worker.json"

DEFAULT_MAX_CONCURRENT_ISSUES = 1

DEFAULT_LEASE_MINUTES = 120

MIN_LEASE_MINUTES = 15

MAX_LEASE_MINUTES = 24 * 60

MAX_CONCURRENT_ISSUES = 16

WORKER_ID_ENV = "AUTODEV_WORKER_ID"

_WORKER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")

_ZERO_SHA = "0" * 40

class ClaimError(RuntimeError):
    pass

@dataclass(frozen=True)
class ClaimPolicy:
    max_concurrent_issues: int = DEFAULT_MAX_CONCURRENT_ISSUES
    lease_minutes: int = DEFAULT_LEASE_MINUTES

@dataclass(frozen=True)
class Claim:
    repository: str
    issue_number: int
    worker_id: str
    run_id: str
    claim_id: str
    acquired_at: str
    heartbeat_at: str
    lease_seconds: int
    ref: str
    sha: str

    def to_json(self) -> dict[str, object]:
        return asdict(self)

@dataclass(frozen=True)
class ClaimAttempt:
    state: str
    claim: Claim | None = None
    owner: Claim | None = None
    detail: str = ""

@dataclass(frozen=True)
class RecoveryResult:
    recovered: tuple[int, ...] = ()
    protected: tuple[int, ...] = ()
    raced: tuple[int, ...] = ()

@dataclass(frozen=True)
class WorkerIdentity:
    worker_id: str

    def to_json(self) -> dict[str, object]:
        return {"schema_version": WORKER_SCHEMA, "worker_id": self.worker_id}

def _now() -> datetime:
    return datetime.now(timezone.utc)

def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

def _parse_time(value: str) -> datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ClaimError(f"invalid claim timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)

def claim_ref(issue_number: int) -> str:
    if issue_number <= 0:
        raise ClaimError("claim issue number must be positive")
    return f"{CLAIM_REF_PREFIX}{issue_number}"
