from __future__ import annotations

import json
from pathlib import Path

from automation.scheduler_health_contract import (
    HEALTH_FILE,
    NOTIFICATION_BACKENDS,
    NOTIFICATION_FILE,
    NOTIFICATION_OFF,
    NOTIFICATION_SCHEMA,
    NotificationPolicy,
    SchedulerHealthError,
)

def health_path(registration_file: Path) -> Path:
    return registration_file.expanduser().resolve().parent / HEALTH_FILE

def notification_path(registration_file: Path) -> Path:
    return registration_file.expanduser().resolve().parent / NOTIFICATION_FILE

def _read_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}

def _write_json(path: Path, value: dict[str, object]) -> None:
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    try:
        temp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temp.replace(path)
    except OSError as exc:
        temp.unlink(missing_ok=True)
        raise SchedulerHealthError(f"cannot write scheduler health state {path}: {exc}") from exc

def load_notification_policy(registration_file: Path) -> NotificationPolicy:
    raw = _read_json(notification_path(registration_file))
    if not raw:
        return NotificationPolicy()
    if raw.get("schema_version") != NOTIFICATION_SCHEMA:
        raise SchedulerHealthError("unsupported scheduler notification policy schema")
    backend = str(raw.get("backend", NOTIFICATION_OFF)).casefold()
    if backend not in NOTIFICATION_BACKENDS:
        raise SchedulerHealthError(f"unsupported scheduler notification backend: {backend}")
    reminder_hours = int(raw.get("reminder_hours", 0) or 0)
    if reminder_hours < 0 or reminder_hours > 24 * 365:
        raise SchedulerHealthError("notification reminder hours must be between 0 and 8760")
    return NotificationPolicy(backend=backend, reminder_hours=reminder_hours)

def save_notification_policy(registration_file: Path, policy: NotificationPolicy) -> None:
    if policy.backend not in NOTIFICATION_BACKENDS:
        raise SchedulerHealthError(f"unsupported scheduler notification backend: {policy.backend}")
    if policy.reminder_hours < 0 or policy.reminder_hours > 24 * 365:
        raise SchedulerHealthError("notification reminder hours must be between 0 and 8760")
    _write_json(notification_path(registration_file), policy.to_json())
