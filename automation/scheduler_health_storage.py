from __future__ import annotations

import json
from pathlib import Path

from automation import notification_storage

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
    try:
        return notification_storage.load_policy_path(notification_path(registration_file))
    except notification_storage.NotificationError as exc:
        raise SchedulerHealthError(str(exc)) from exc

def save_notification_policy(registration_file: Path, policy: NotificationPolicy) -> None:
    try:
        notification_storage.save_policy_path(notification_path(registration_file), policy)
    except notification_storage.NotificationError as exc:
        raise SchedulerHealthError(str(exc)) from exc
