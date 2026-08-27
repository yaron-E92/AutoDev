from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from automation import repository_identity, workflow_stages
from automation.notification_contract import (
    EVENT_BLOCKED,
    EVENT_FAILED,
    EVENT_READY_FOR_REVIEW,
    MODE_MANUAL,
    NotificationEvent,
    NotificationResult,
)
from automation import notification_events, notification_storage
from automation.workflow_storage import read_json, write_json


_TERMINAL_EVENT = {
    "PR_READY": EVENT_READY_FOR_REVIEW,
    "BLOCKED": EVENT_BLOCKED,
    "ATTENTION_REQUIRED": EVENT_BLOCKED,
    "FAILED": EVENT_FAILED,
}


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_fingerprint(payload: dict[str, object], event: str) -> str:
    source = {
        "event": event,
        "issue_number": int(payload.get("issue_number", 0) or 0),
        "stage": str(
            payload.get("failed_stage", "")
            or payload.get("stage", "")
            or payload.get("completed_stage", "")
        ),
        "failure_classification": str(payload.get("failure_classification", "")),
        "failure_fingerprint": str(payload.get("failure_fingerprint", "")),
        "verified_source_identity": str(payload.get("verified_source_identity", "")),
        "created_tree_sha": str(payload.get("created_tree_sha", "")),
        "pr_head_sha": str(payload.get("pr_head_sha", "")),
        "pr_url": str(payload.get("pr_url", "")),
    }
    return hashlib.sha256(
        json.dumps(source, sort_keys=True, separators=(",", ":")).encode(
            "utf-8",
            errors="replace",
        )
    ).hexdigest()


def event_from_run_payload(
    repository: str,
    payload: dict[str, object],
) -> NotificationEvent | None:
    state = str(payload.get("state", ""))
    event = _TERMINAL_EVENT.get(state)
    if event is None:
        return None
    issue = int(payload.get("issue_number", 0) or 0)
    stage = str(
        payload.get("failed_stage", "")
        or payload.get("stage", "")
        or payload.get("completed_stage", "")
    )
    reason_code = str(payload.get("failure_classification", "") or state.casefold())
    pr_url = str(payload.get("pr_url", ""))

    if event == EVENT_READY_FOR_REVIEW:
        summary = f"Issue #{issue} is ready for review." if issue else "Issue-to-PR work is ready for review."
        if pr_url:
            summary += f" PR: {pr_url}"
    elif event == EVENT_BLOCKED:
        summary = f"Issue #{issue} requires attention." if issue else "AutoDev work requires attention."
        if stage:
            summary += f" Stage: {stage}."
        if reason_code:
            summary += f" Reason: {reason_code}."
    else:
        summary = f"Issue #{issue} failed." if issue else "AutoDev issue-to-PR work failed."
        if stage:
            summary += f" Stage: {stage}."
        if reason_code:
            summary += f" Reason: {reason_code}."

    return NotificationEvent(
        repository=repository,
        mode=MODE_MANUAL,
        event=event,
        fingerprint=_safe_fingerprint(payload, event),
        observed_at=_iso_now(),
        issue_number=issue,
        stage=stage,
        reason_code=reason_code,
        pr_url=pr_url,
        summary=summary,
        notify_initial=True,
        notify_transition=True,
        reminder_eligible=event == EVENT_BLOCKED,
    )


def _record_diagnostic(
    repo: Path,
    payload: dict[str, object],
    event: NotificationEvent | None,
    result: NotificationResult,
) -> None:
    if (
        int(payload.get("requested_issue_number", 0) or 0) > 0
        and payload.get("new_run_prepared") is False
    ):
        # The current directory belongs to a different preserved run. Delivery
        # state is already stored in the user-local notification event store;
        # do not write issue-N notification diagnostics into issue-M state.
        return
    current = repo / workflow_stages.CURRENT_DIR
    if not current.is_dir():
        return
    path = current / workflow_stages.DIAGNOSTICS_FILE
    value = read_json(path)
    diagnostics = value if isinstance(value, dict) else {}
    diagnostics["notification_outcome"] = {
        "mode": MODE_MANUAL,
        "event": event.event if event else "",
        "fingerprint": event.fingerprint if event else "",
        **result.to_json(),
    }
    try:
        write_json(path, diagnostics)
    except OSError:
        pass


def best_effort_notify_run_outcome(
    repo: Path,
    payload: dict[str, object],
    *,
    home: Path | None = None,
    runner: Callable[..., object] = subprocess.run,
    which: Callable[[str], str | None] = shutil.which,
    notifier=None,
    platform_name: str | None = None,
) -> NotificationResult:
    repo = repo.expanduser().resolve()
    if os.environ.get("AUTODEV_HEADLESS", "").strip():
        return NotificationResult(False, False, "off", "scheduled-health-owns-delivery")

    event: NotificationEvent | None = None
    try:
        repository = repository_identity.resolve_github_repository(repo, runner=runner)
        event = event_from_run_payload(repository, payload)
        if event is None:
            result = NotificationResult(False, False, "off", "non-notifiable-run-state")
        else:
            policy = notification_storage.load_policy(repository, home=home)
            result = notification_events.observe_event(
                notification_storage.event_state_path(repository, home=home),
                event,
                policy=policy,
                notifier=notifier,
                runner=runner,
                which=which,
                platform_name=platform_name,
            )
    except Exception as exc:
        result = NotificationResult(
            True,
            False,
            "native",
            f"notification reporting failed: {type(exc).__name__}",
        )
    _record_diagnostic(repo, payload, event, result)
    return result
