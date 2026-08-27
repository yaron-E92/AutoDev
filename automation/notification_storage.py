from __future__ import annotations

import json
from pathlib import Path

from automation.notification_contract import (
    NOTIFICATION_BACKENDS,
    NOTIFICATION_EVENT_SCHEMA,
    NOTIFICATION_OFF,
    NOTIFICATION_POLICY_SCHEMA,
    NotificationError,
    NotificationPolicy,
)
from automation.scheduler_types import registration_path


POLICY_FILE = "notifications.json"
EVENT_STATE_FILE = "notification-events.json"


def notification_root(github_repository: str, *, home: Path | None = None) -> Path:
    return registration_path(github_repository, home=home).parent


def policy_path(github_repository: str, *, home: Path | None = None) -> Path:
    return notification_root(github_repository, home=home) / POLICY_FILE


def event_state_path(github_repository: str, *, home: Path | None = None) -> Path:
    return notification_root(github_repository, home=home) / EVENT_STATE_FILE


def _read_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_json(path: Path, value: dict[str, object]) -> None:
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        temporary.write_text(
            json.dumps(value, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise NotificationError(f"cannot write AutoDev notification state {path}: {exc}") from exc


def load_policy_path(path: Path) -> NotificationPolicy:
    raw = _read_json(path)
    if not raw:
        return NotificationPolicy()
    if raw.get("schema_version") != NOTIFICATION_POLICY_SCHEMA:
        raise NotificationError("unsupported AutoDev notification policy schema")
    backend = str(raw.get("backend", NOTIFICATION_OFF)).casefold()
    if backend not in NOTIFICATION_BACKENDS:
        raise NotificationError(f"unsupported AutoDev notification backend: {backend}")
    reminder_hours = int(raw.get("reminder_hours", 0) or 0)
    if reminder_hours < 0 or reminder_hours > 24 * 365:
        raise NotificationError("notification reminder hours must be between 0 and 8760")
    return NotificationPolicy(backend=backend, reminder_hours=reminder_hours)


def save_policy_path(path: Path, policy: NotificationPolicy) -> None:
    if policy.backend not in NOTIFICATION_BACKENDS:
        raise NotificationError(f"unsupported AutoDev notification backend: {policy.backend}")
    if policy.reminder_hours < 0 or policy.reminder_hours > 24 * 365:
        raise NotificationError("notification reminder hours must be between 0 and 8760")
    _write_json(path, policy.to_json())


def load_policy(github_repository: str, *, home: Path | None = None) -> NotificationPolicy:
    return load_policy_path(policy_path(github_repository, home=home))


def save_policy(
    github_repository: str,
    policy: NotificationPolicy,
    *,
    home: Path | None = None,
) -> None:
    save_policy_path(policy_path(github_repository, home=home), policy)


def load_event_state_path(path: Path) -> dict[str, object]:
    raw = _read_json(path)
    if not raw:
        return {"schema_version": NOTIFICATION_EVENT_SCHEMA, "modes": {}}
    if raw.get("schema_version") != NOTIFICATION_EVENT_SCHEMA:
        raise NotificationError("unsupported AutoDev notification event-state schema")
    modes = raw.get("modes", {})
    if not isinstance(modes, dict):
        raise NotificationError("invalid AutoDev notification event-state modes")
    return raw
