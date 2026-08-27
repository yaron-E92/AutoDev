from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Callable

from automation import notification_delivery, notification_events, notification_storage
from automation.notification_contract import (
    EVENT_BLOCKED,
    EVENT_FAILED,
    EVENT_READY_FOR_REVIEW,
    EVENT_SCHEDULER_HEALTH,
    MODE_SCHEDULED,
    NotificationEvent,
    NotificationPolicy,
    NotificationResult,
)
from automation.scheduler_health_contract import (
    HEALTH_SCHEMA,
    HEALTH_STATES,
    HealthSnapshot,
    REMINDER_STATES,
    SchedulerHealthError,
    _now,
)
from automation.scheduler_health_probes import render_health
from automation.scheduler_health_storage import (
    _read_json,
    _write_json,
    health_path,
    load_notification_policy,
)


_INITIAL_ACTIONABLE_STATES = {
    "ATTENTION_REQUIRED",
    "SCHEDULER_ERROR",
    "ALL_MANAGED_WORK_BLOCKED",
    "PR_READY",
}


def _event_kind(snapshot: HealthSnapshot) -> str:
    if snapshot.state == "PR_READY":
        return EVENT_READY_FOR_REVIEW
    if snapshot.state in {"ATTENTION_REQUIRED", "ALL_MANAGED_WORK_BLOCKED"}:
        return EVENT_BLOCKED
    if snapshot.state == "SCHEDULER_ERROR":
        return EVENT_FAILED
    return EVENT_SCHEDULER_HEALTH


def _event_from_snapshot(snapshot: HealthSnapshot) -> NotificationEvent:
    return NotificationEvent(
        repository=snapshot.repository,
        mode=MODE_SCHEDULED,
        event=_event_kind(snapshot),
        fingerprint=snapshot.fingerprint,
        observed_at=snapshot.observed_at,
        issue_number=snapshot.issue_number,
        stage=snapshot.next_stage or snapshot.state,
        reason_code=snapshot.attention_kind or snapshot.state,
        summary=render_health(snapshot),
        notify_initial=snapshot.state in _INITIAL_ACTIONABLE_STATES,
        notify_transition=True,
        reminder_eligible=snapshot.state in REMINDER_STATES,
    )


def _notification_message(snapshot: HealthSnapshot) -> tuple[str, str]:
    return notification_events.render_event(_event_from_snapshot(snapshot))


def _native_notify(
    title: str,
    message: str,
    *,
    runner=None,
    which=None,
    platform_name: str | None = None,
) -> NotificationResult:
    kwargs = {"platform_name": platform_name}
    if runner is not None:
        kwargs["runner"] = runner
    if which is not None:
        kwargs["which"] = which
    return notification_delivery.native_notify(title, message, **kwargs)


def _snapshot_from_json(raw: object) -> HealthSnapshot | None:
    if not isinstance(raw, dict):
        return None
    state = str(raw.get("state", ""))
    if state not in HEALTH_STATES:
        return None
    queue = raw.get("queue", {})
    privacy_counts = raw.get("privacy_grants", {})
    blocker_counts = raw.get("blocker_counts", {})
    if (
        not isinstance(queue, dict)
        or not isinstance(privacy_counts, dict)
        or not isinstance(blocker_counts, dict)
    ):
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
    notification_policy = policy or load_notification_policy(registration_file)
    event_path = registration_file.expanduser().resolve().parent / notification_storage.EVENT_STATE_FILE
    try:
        notification = notification_events.observe_event(
            event_path,
            _event_from_snapshot(snapshot),
            policy=notification_policy,
            notifier=notifier,
            now=now or _now(),
        )
        event_state = notification_storage.load_event_state_path(event_path)
        modes = event_state.get("modes", {})
        scheduled = modes.get(MODE_SCHEDULED, {}) if isinstance(modes, dict) else {}
        last_notification = (
            scheduled.get("last_notification", {})
            if isinstance(scheduled, dict)
            else {}
        )
        notification_record = (
            dict(last_notification)
            if isinstance(last_notification, dict)
            else {}
        )
    except notification_storage.NotificationError as exc:
        raise SchedulerHealthError(str(exc)) from exc

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
