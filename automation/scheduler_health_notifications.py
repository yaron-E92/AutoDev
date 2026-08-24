from __future__ import annotations

import os
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, TextIO

from automation.scheduler_health_contract import (
    HEALTH_SCHEMA,
    HEALTH_STATES,
    HealthSnapshot,
    NOTIFICATION_NATIVE,
    NotificationPolicy,
    NotificationResult,
    REMINDER_STATES,
    SchedulerHealthError,
    _iso,
    _now,
    _parse_time,
)
from automation.scheduler_health_probes import (
    render_health,
)
from automation.scheduler_health_storage import (
    _read_json,
    _write_json,
    health_path,
    load_notification_policy,
)

def _notification_message(snapshot: HealthSnapshot) -> tuple[str, str]:
    title = f"AutoDev · {snapshot.repository}"
    # render_health is deliberately bounded to deterministic metadata only.
    return title, render_health(snapshot)

def _native_notify(
    title: str,
    message: str,
    *,
    runner: Callable[..., object] = subprocess.run,
    which: Callable[[str], str | None] = shutil.which,
    platform_name: str | None = None,
) -> NotificationResult:
    platform = (platform_name or ("windows" if os.name == "nt" else "posix")).casefold()
    if platform == "windows":
        executable = which("msg") or which("msg.exe")
        if not executable:
            return NotificationResult(True, False, NOTIFICATION_NATIVE, "msg.exe is unavailable")
        argv = [executable, "*", "/TIME:10", f"{title}: {message}"]
    else:
        executable = which("notify-send")
        if not executable:
            return NotificationResult(True, False, NOTIFICATION_NATIVE, "notify-send is unavailable")
        argv = [executable, title, message]
    try:
        completed = runner(
            argv,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
        )
    except OSError:
        return NotificationResult(True, False, NOTIFICATION_NATIVE, "native notifier could not be launched")
    if int(getattr(completed, "returncode", 1)) != 0:
        return NotificationResult(True, False, NOTIFICATION_NATIVE, "native notifier returned a nonzero exit code")
    return NotificationResult(True, True, NOTIFICATION_NATIVE)

def _should_notify(
    previous: HealthSnapshot | None,
    current: HealthSnapshot,
    record: dict[str, object],
    policy: NotificationPolicy,
    *,
    now: datetime,
) -> tuple[bool, str]:
    if not policy.enabled:
        return False, "notifications-disabled"
    if previous is None:
        if current.state in {"ATTENTION_REQUIRED", "SCHEDULER_ERROR", "ALL_MANAGED_WORK_BLOCKED", "PR_READY"}:
            return True, "initial-actionable-state"
        return False, "initial-benign-state"
    if previous.fingerprint != current.fingerprint:
        return True, "material-transition"
    if current.state not in REMINDER_STATES or policy.reminder_hours <= 0:
        return False, "unchanged-state"
    last_notification = record.get("last_notification", {})
    last_notification = last_notification if isinstance(last_notification, dict) else {}
    last_at = _parse_time(last_notification.get("at"))
    if last_at is None or now - last_at >= timedelta(hours=policy.reminder_hours):
        return True, "attention-reminder-cooldown"
    return False, "cooldown-active"

def _snapshot_from_json(raw: object) -> HealthSnapshot | None:
    if not isinstance(raw, dict):
        return None
    state = str(raw.get("state", ""))
    if state not in HEALTH_STATES:
        return None
    queue = raw.get("queue", {})
    privacy_counts = raw.get("privacy_grants", {})
    blocker_counts = raw.get("blocker_counts", {})
    if not isinstance(queue, dict) or not isinstance(privacy_counts, dict) or not isinstance(blocker_counts, dict):
        return None
    return HealthSnapshot(
        state=state,
        repository=str(raw.get("repository", "")),
        observed_at=str(raw.get("observed_at", "")),
        fingerprint=str(raw.get("fingerprint", "")),
        queue={str(k): int(v) for k, v in queue.items()},
        unmanaged_open=int(raw.get("unmanaged_open", 0) or 0),
        issue_number=int(raw.get("issue_number", 0) or 0),
        run_state=str(raw.get("run_state", "")),
        next_stage=str(raw.get("next_stage", "")),
        next_action=str(raw.get("next_action", "")),
        last_outcome=str(raw.get("last_outcome", "")),
        attention_kind=str(raw.get("attention_kind", "")),
        privacy_grants={str(k): int(v) for k, v in privacy_counts.items()},
        blocker_counts={str(k): int(v) for k, v in blocker_counts.items()},
    )

def observe_health(
    registration_file: Path,
    snapshot: HealthSnapshot,
    *,
    policy: NotificationPolicy | None = None,
    notifier: Callable[[str, str], NotificationResult] | None = None,
    now: datetime | None = None,
) -> NotificationResult:
    path = health_path(registration_file)
    record = _read_json(path)
    if record and record.get("schema_version") != HEALTH_SCHEMA:
        raise SchedulerHealthError("unsupported scheduler health state schema")
    previous = _snapshot_from_json(record.get("current"))
    current_time = (now or _now()).astimezone(timezone.utc)
    notification_policy = policy or load_notification_policy(registration_file)
    should_notify, reason = _should_notify(previous, snapshot, record, notification_policy, now=current_time)
    notification = NotificationResult(False, False, notification_policy.backend, reason)
    if should_notify:
        title, message = _notification_message(snapshot)
        if notifier is not None:
            try:
                notification = notifier(title, message)
            except Exception:
                notification = NotificationResult(True, False, notification_policy.backend, "notification delivery raised an exception")
        elif notification_policy.backend == NOTIFICATION_NATIVE:
            notification = _native_notify(title, message)
        else:
            notification = NotificationResult(False, False, notification_policy.backend, "notifications-disabled")
        notification_record = {
            "at": _iso(current_time),
            "fingerprint": snapshot.fingerprint,
            "state": snapshot.state,
            "backend": notification.backend,
            "delivered": notification.delivered,
            "reason": reason,
        }
    else:
        prior = record.get("last_notification")
        notification_record = dict(prior) if isinstance(prior, dict) else {}

    previous_state = previous.state if previous else ""
    transition = record.get("last_transition")
    transition_record = dict(transition) if isinstance(transition, dict) else {}
    if previous is None or previous.fingerprint != snapshot.fingerprint:
        transition_record = {
            "at": snapshot.observed_at,
            "from": previous_state,
            "to": snapshot.state,
            "fingerprint": snapshot.fingerprint,
        }

    payload = {
        "schema_version": HEALTH_SCHEMA,
        "current": snapshot.to_json(),
        "last_transition": transition_record,
        "last_notification": notification_record,
    }
    _write_json(path, payload)
    return notification
