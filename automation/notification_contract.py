from __future__ import annotations

from dataclasses import asdict, dataclass


NOTIFICATION_POLICY_SCHEMA = 1
NOTIFICATION_EVENT_SCHEMA = 1

NOTIFICATION_OFF = "off"
NOTIFICATION_NATIVE = "native"
NOTIFICATION_BACKENDS = (NOTIFICATION_OFF, NOTIFICATION_NATIVE)

MODE_MANUAL = "manual"
MODE_SCHEDULED = "scheduled"
NOTIFICATION_MODES = (MODE_MANUAL, MODE_SCHEDULED)

EVENT_READY_FOR_REVIEW = "ready-for-review"
EVENT_BLOCKED = "blocked"
EVENT_FAILED = "failed"
EVENT_SCHEDULER_HEALTH = "scheduler-health"
NOTIFICATION_EVENTS = (
    EVENT_READY_FOR_REVIEW,
    EVENT_BLOCKED,
    EVENT_FAILED,
    EVENT_SCHEDULER_HEALTH,
)


class NotificationError(RuntimeError):
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
            "schema_version": NOTIFICATION_POLICY_SCHEMA,
            "backend": self.backend,
            "reminder_hours": self.reminder_hours,
        }


@dataclass(frozen=True)
class NotificationResult:
    attempted: bool
    delivered: bool
    backend: str
    reason: str = ""

    def to_json(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class NotificationEvent:
    repository: str
    mode: str
    event: str
    fingerprint: str
    observed_at: str
    issue_number: int = 0
    stage: str = ""
    reason_code: str = ""
    pr_url: str = ""
    summary: str = ""
    notify_initial: bool = True
    notify_transition: bool = True
    reminder_eligible: bool = False

    def to_json(self) -> dict[str, object]:
        return asdict(self)
