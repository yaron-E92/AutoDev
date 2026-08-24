from __future__ import annotations

from automation import privacy_grant_cli, privacy_grant_commands, privacy_grant_matching, queue_contract, queue_github, queue_presentation, queue_workflow

import hashlib
import json
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, TextIO
from automation import privacy, queue_selection, scheduler, workflow_stages

from automation.scheduler_health_contract import (
    HealthSnapshot,
    _iso,
    _now,
)

def _privacy_grant_summary(repo: Path) -> dict[str, int]:
    counts = {"active": 0, "expired": 0, "revoked": 0}
    for record in privacy_grant_commands.current_grants(repo):
        status = str(record.get("status", ""))
        if status in counts:
            counts[status] += 1
    return counts

def _privacy_probe(
    repo: Path,
    *,
    runner: Callable[..., object] = subprocess.run,
    which: Callable[[str], str | None] = shutil.which,
) -> tuple[bool, dict[str, int]]:
    counts = _privacy_grant_summary(repo)
    policy = privacy.load_policy(repo)
    if not policy.enabled or policy.local_only or policy.consent_mode != "explicit":
        return False, counts
    try:
        required = privacy_grant_cli._resolve_requirements(repo, runner=runner, which=which)  # type: ignore[attr-defined]
    except Exception:
        # Health remains useful even when optional route introspection is unavailable.
        # The actual coordinator privacy gate still fails closed before any model call.
        return False, counts
    uncovered = [
        item
        for item in required
        if privacy_grant_matching.matching_grant(repo, policy, item) is None
    ]
    return bool(uncovered), counts

def _raw_run_status(repo: Path) -> tuple[str, int]:
    current = repo.expanduser().resolve() / workflow_stages.CURRENT_DIR
    if not current.is_dir():
        return "", 0
    try:
        state = workflow_stages.read_state(current)
    except Exception:
        return "", 0
    return (
        str(state.get("Status", "")),
        int(state.get("IssueNumber", 0) or 0),
    )

def _blocker_counts(states: list[queue_contract.QueueState]) -> dict[str, int]:
    counts: dict[int, int] = {}
    for state in states:
        if state.reason != "blocked":
            continue
        for blocker in state.open_blockers:
            counts[blocker.number] = counts.get(blocker.number, 0) + 1
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return {str(number): count for number, count in ordered}

def _first_issue_number(states: list[queue_contract.QueueState], reason: str) -> int:
    return min(
        (state.issue.number for state in states if state.reason == reason),
        default=0,
    )

def _fingerprint_source(
    *,
    state: str,
    repository: str,
    queue: dict[str, int],
    unmanaged_open: int,
    issue_number: int,
    run_state: str,
    next_stage: str,
    next_action: str,
    last_outcome: str,
    attention_kind: str,
    privacy_grants: dict[str, int],
    blocker_counts: dict[str, int],
) -> dict[str, object]:
    return {
        "state": state,
        "repository": repository,
        "queue": queue,
        "unmanaged_open": unmanaged_open,
        "issue_number": issue_number,
        "run_state": run_state,
        "next_stage": next_stage,
        "next_action": next_action,
        "last_outcome": last_outcome,
        "attention_kind": attention_kind,
        "privacy_grants": privacy_grants,
        "blocker_counts": blocker_counts,
    }

def _fingerprint(value: dict[str, object]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()

def compute_health(
    repo: Path,
    github_repo: str,
    *,
    runner: Callable[..., object] = subprocess.run,
    which: Callable[[str], str | None] = shutil.which,
    now: datetime | None = None,
    force_error: bool = False,
    last_outcome: str = "",
    privacy_probe: Callable[[Path], tuple[bool, dict[str, int]]] | None = None,
) -> HealthSnapshot:
    repo = repo.expanduser().resolve()
    states = queue_workflow.inspect_queue(repo, github_repo, runner=runner)
    summary = queue_presentation.queue_summary(states)
    issues = queue_github.list_issues(repo, github_repo, runner=runner)
    unmanaged_open = sum(
        issue.state == "open" and queue_contract.MANAGED_LABEL not in issue.labels
        for issue in issues
    )
    existing = queue_selection.inspect_existing_run(repo)
    raw_status, raw_issue_number = _raw_run_status(repo)
    probe = privacy_probe or (lambda path: _privacy_probe(path, runner=runner, which=which))
    privacy_blocked, grant_counts = probe(repo)
    blockers = _blocker_counts(states)

    issue_number = existing.issue_number or raw_issue_number
    attention_kind = ""
    normalized_raw = raw_status.casefold().replace("_", "").replace("-", "")
    normalized_outcome = last_outcome.casefold().replace("_", "").replace("-", "")

    if privacy_blocked and (summary["ready"] > 0 or existing.state == "RESUME_EXISTING"):
        state = "ATTENTION_REQUIRED"
        attention_kind = "privacy-consent"
    elif normalized_raw in {"readyforreview", "prready"} or normalized_outcome in {
        "readyforreview",
        "prready",
    }:
        state = "PR_READY"
    elif existing.state == "ATTENTION_REQUIRED" or summary["attention_required"] > 0:
        state = "ATTENTION_REQUIRED"
        attention_kind = "manual-or-queue-attention"
    elif force_error or existing.state == "RUN_HEALTH_BLOCKED":
        state = "SCHEDULER_ERROR"
    elif existing.state == "RESUME_EXISTING" or summary["running"] > 0:
        state = "RUNNING_OR_RESUMABLE"
    elif summary["ready"] > 0:
        state = "READY_WORK_AVAILABLE"
    elif summary["managed"] > 0 and summary["dependency_blocked"] == summary["managed"]:
        state = "ALL_MANAGED_WORK_BLOCKED"
    elif summary["policy_excluded"] > 0:
        state = "ATTENTION_REQUIRED"
        attention_kind = "repository-policy"
    else:
        state = "NO_READY_WORK"

    if not issue_number:
        if state == "ATTENTION_REQUIRED" and attention_kind == "privacy-consent":
            issue_number = _first_issue_number(states, "ready")
        elif state == "ATTENTION_REQUIRED":
            issue_number = _first_issue_number(states, "attention")
        elif state == "RUNNING_OR_RESUMABLE":
            issue_number = _first_issue_number(states, "running")

    source = _fingerprint_source(
        state=state,
        repository=github_repo,
        queue=summary,
        unmanaged_open=unmanaged_open,
        issue_number=issue_number,
        run_state=existing.state,
        next_stage=existing.next_stage,
        next_action=existing.next_action,
        last_outcome=last_outcome,
        attention_kind=attention_kind,
        privacy_grants=grant_counts,
        blocker_counts=blockers,
    )
    return HealthSnapshot(
        state=state,
        repository=github_repo,
        observed_at=_iso(now or _now()),
        fingerprint=_fingerprint(source),
        queue=summary,
        unmanaged_open=unmanaged_open,
        issue_number=issue_number,
        run_state=existing.state,
        next_stage=existing.next_stage,
        next_action=existing.next_action,
        last_outcome=last_outcome,
        attention_kind=attention_kind,
        privacy_grants=grant_counts,
        blocker_counts=blockers,
    )

def render_health(snapshot: HealthSnapshot) -> str:
    queue = snapshot.queue
    prefix = (
        f"{queue.get('ready', 0)} ready, "
        f"{queue.get('dependency_blocked', 0)} dependency-blocked, "
        f"{snapshot.unmanaged_open} unmanaged open issue(s)"
    )
    if snapshot.state == "READY_WORK_AVAILABLE":
        return f"READY_WORK_AVAILABLE: {prefix}."
    if snapshot.state == "RUNNING_OR_RESUMABLE":
        stage = snapshot.next_stage or "durable checkpoint"
        issue = f"Issue #{snapshot.issue_number} " if snapshot.issue_number else "AutoDev run "
        return f"RUNNING_OR_RESUMABLE: {issue}is safely resumable from {stage}."
    if snapshot.state == "NO_READY_WORK":
        return f"NO_READY_WORK: {prefix}."
    if snapshot.state == "ALL_MANAGED_WORK_BLOCKED":
        top = next(iter((snapshot.blocker_counts or {}).items()), None)
        suffix = f" Top blocker #{top[0]} blocks {top[1]} managed issue(s)." if top else ""
        return (
            f"ALL_MANAGED_WORK_BLOCKED: all {queue.get('managed', 0)} managed open issue(s) "
            f"are dependency-blocked.{suffix}"
        )
    if snapshot.state == "ATTENTION_REQUIRED":
        issue = f"Issue #{snapshot.issue_number} " if snapshot.issue_number else "AutoDev "
        if snapshot.attention_kind == "privacy-consent":
            return (
                f"ATTENTION_REQUIRED: {issue}requires privacy consent before autonomous model work; "
                "the privacy gate prevents model content from being sent without authorization."
            )
        return f"ATTENTION_REQUIRED: {issue}requires developer attention before autonomous work can continue."
    if snapshot.state == "PR_READY":
        issue = f"Issue #{snapshot.issue_number} " if snapshot.issue_number else "AutoDev work "
        return f"PR_READY: {issue}is ready for review/merge."
    return "SCHEDULER_ERROR: the autonomous scheduler or durable run requires inspection."
