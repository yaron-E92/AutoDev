from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone


HEALTH_SCHEMA = 1

NOTIFICATION_SCHEMA = 1

HEALTH_FILE = "health.json"

NOTIFICATION_FILE = "notifications.json"

NOTIFICATION_OFF = "off"

NOTIFICATION_NATIVE = "native"

NOTIFICATION_BACKENDS = (NOTIFICATION_OFF, NOTIFICATION_NATIVE)

REMINDER_STATES = {"ATTENTION_REQUIRED", "SCHEDULER_ERROR"}

HEALTH_STATES = {
    "READY_WORK_AVAILABLE",
    "RUNNING_OR_RESUMABLE",
    "NO_READY_WORK",
    "ALL_MANAGED_WORK_BLOCKED",
    "ATTENTION_REQUIRED",
    "PR_READY",
    "SCHEDULER_ERROR",
}

class SchedulerHealthError(RuntimeError):
    pass

@dataclass(frozen=True)
class NotificationPolicy:
    backend: str = NOTIFICATION_OFF
    reminder_hours: int = 0

    @property
    def enabled(self) -> bool:
        return self.backend != NOTIFICATION_OFF

    def to_json(self) -> dict[str, object]:
        return {
            "schema_version": NOTIFICATION_SCHEMA,
            "backend": self.backend,
            "reminder_hours": self.reminder_hours,
        }

@dataclass(frozen=True)
class HealthSnapshot:
    state: str
    repository: str
    observed_at: str
    fingerprint: str
    queue: dict[str, int]
    unmanaged_open: int
    issue_number: int = 0
    run_state: str = ""
    next_stage: str = ""
    next_action: str = ""
    last_outcome: str = ""
    attention_kind: str = ""
    privacy_grants: dict[str, int] | None = None
    blocker_counts: dict[str, int] | None = None

    def to_json(self) -> dict[str, object]:
        value = asdict(self)
        value["privacy_grants"] = dict(self.privacy_grants or {})
        value["blocker_counts"] = dict(self.blocker_counts or {})
        return value

@dataclass(frozen=True)
class NotificationResult:
    attempted: bool
    delivered: bool
    backend: str
    reason: str = ""

    def to_json(self) -> dict[str, object]:
        return asdict(self)

def _now() -> datetime:
    return datetime.now(timezone.utc)

def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

def _parse_time(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
