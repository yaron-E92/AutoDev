from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from automation.notification_contract import (
    MODE_MANUAL,
    MODE_SCHEDULED,
    NOTIFICATION_EVENTS,
    NOTIFICATION_MODES,
    NOTIFICATION_NATIVE,
    NotificationError,
    NotificationEvent,
    NotificationPolicy,
    NotificationResult,
)
from automation.notification_delivery import native_notify
from automation.notification_storage import (
    _write_json,
    load_event_state_path,
)


MAX_TITLE_CHARS = 160
MAX_MESSAGE_CHARS = 700


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


def validate_event(event: NotificationEvent) -> None:
    if event.mode not in NOTIFICATION_MODES:
        raise NotificationError(f"unsupported notification mode: {event.mode}")
    if event.event not in NOTIFICATION_EVENTS:
        raise NotificationError(f"unsupported notification event: {event.event}")
    if not event.repository or "/" not in event.repository:
        raise NotificationError("notification repository identity is missing or invalid")
    if not event.fingerprint:
        raise NotificationError("notification event fingerprint is required")


def render_event(event: NotificationEvent) -> tuple[str, str]:
    validate_event(event)
    title = f"AutoDev · {event.repository}"[:MAX_TITLE_CHARS]
    if event.summary.strip():
        message = event.summary.strip()
    else:
        parts = [event.event]
        if event.issue_number:
            parts.append(f"issue #{event.issue_number}")
        if event.stage:
            parts.append(f"stage={event.stage}")
        if event.reason_code:
            parts.append(f"reason={event.reason_code}")
        if event.pr_url:
            parts.append(f"PR={event.pr_url}")
        message = "; ".join(parts)
    return title, message[:MAX_MESSAGE_CHARS]


def _mode_record(state: dict[str, object], mode: str) -> dict[str, object]:
    modes = state.setdefault("modes", {})
    if not isinstance(modes, dict):
        raise NotificationError("invalid notification state modes")
    value = modes.get(mode, {})
    return dict(value) if isinstance(value, dict) else {}


def _should_notify(
    previous: dict[str, object] | None,
    event: NotificationEvent,
    record: dict[str, object],
    policy: NotificationPolicy,
    *,
    now: datetime,
) -> tuple[bool, str]:
    if not policy.enabled:
        return False, "notifications-disabled"
    if previous is None:
        return (event.notify_initial, "initial-actionable-state" if event.notify_initial else "initial-benign-state")
    if str(previous.get("fingerprint", "")) != event.fingerprint:
        return (
            event.notify_transition,
            "material-transition" if event.notify_transition else "transition-suppressed",
        )
    if not event.reminder_eligible or policy.reminder_hours <= 0:
        return False, "unchanged-state"
    last_notification = record.get("last_notification", {})
    last_notification = last_notification if isinstance(last_notification, dict) else {}
    last_at = _parse_time(last_notification.get("at"))
    if last_at is None or now - last_at >= timedelta(hours=policy.reminder_hours):
        return True, "attention-reminder-cooldown"
    return False, "cooldown-active"


def observe_event(
    state_path: Path,
    event: NotificationEvent,
    *,
    policy: NotificationPolicy,
    notifier: Callable[[str, str], NotificationResult] | None = None,
    runner=None,
    which=None,
    platform_name: str | None = None,
    now: datetime | None = None,
) -> NotificationResult:
    validate_event(event)
    state = load_event_state_path(state_path)
    record = _mode_record(state, event.mode)
    previous_raw = record.get("current")
    previous = previous_raw if isinstance(previous_raw, dict) else None
    current_time = (now or _now()).astimezone(timezone.utc)
    should_notify, reason = _should_notify(
        previous,
        event,
        record,
        policy,
        now=current_time,
    )
    result = NotificationResult(False, False, policy.backend, reason)
    if should_notify:
        title, message = render_event(event)
        if notifier is not None:
            try:
                result = notifier(title, message)
            except Exception:
                result = NotificationResult(
                    True,
                    False,
                    policy.backend,
                    "notification delivery raised an exception",
                )
        elif policy.backend == NOTIFICATION_NATIVE:
            kwargs = {"platform_name": platform_name}
            if runner is not None:
                kwargs["runner"] = runner
            if which is not None:
                kwargs["which"] = which
            result = native_notify(title, message, **kwargs)
        notification_record = {
            "at": _iso(current_time),
            "fingerprint": event.fingerprint,
            "event": event.event,
            "backend": result.backend,
            "delivered": result.delivered,
            "reason": reason,
        }
    else:
        prior = record.get("last_notification")
        notification_record = dict(prior) if isinstance(prior, dict) else {}

    previous_fingerprint = str(previous.get("fingerprint", "")) if previous else ""
    transition_raw = record.get("last_transition")
    transition_record = dict(transition_raw) if isinstance(transition_raw, dict) else {}
    if previous is None or previous_fingerprint != event.fingerprint:
        transition_record = {
            "at": event.observed_at,
            "from": str(previous.get("event", "")) if previous else "",
            "to": event.event,
            "fingerprint": event.fingerprint,
        }

    record.update(
        {
            "current": event.to_json(),
            "last_transition": transition_record,
            "last_notification": notification_record,
            "last_delivery": result.to_json(),
        }
    )
    modes = state.setdefault("modes", {})
    assert isinstance(modes, dict)
    modes[event.mode] = record
    _write_json(state_path, state)
    return result
