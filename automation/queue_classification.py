from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable, TextIO

from automation.queue_contract import (
    ATTENTION_LABEL,
    BLOCKED_LABEL,
    Blocker,
    MANAGED_LABEL,
    QueueIssue,
    QueuePolicy,
    QueueState,
    READY_LABEL,
    RUNNING_LABEL,
)
from automation.queue_github import (
    _run_gh,
)

def _split_blockers(
    blockers: list[Blocker],
) -> tuple[tuple[Blocker, ...], tuple[Blocker, ...]]:
    open_items = tuple(item for item in blockers if item.state == "open")
    closed_items = tuple(item for item in blockers if item.state != "open")
    return open_items, closed_items

def classify_issue(
    issue: QueueIssue,
    blockers: list[Blocker] | tuple[Blocker, ...],
    policy: QueuePolicy,
) -> QueueState:
    labels = set(issue.labels)
    open_blockers, closed_blockers = _split_blockers(list(blockers))
    if issue.state != "open":
        reason = "closed"
    elif MANAGED_LABEL not in labels:
        reason = "unmanaged"
    elif open_blockers:
        reason = "blocked"
    elif ATTENTION_LABEL in labels:
        reason = "attention"
    elif RUNNING_LABEL in labels:
        reason = "running"
    elif not policy.autonomous_execution:
        reason = "policy-excluded"
    else:
        reason = "ready"
    return QueueState(
        issue=issue,
        reason=reason,
        open_blockers=open_blockers,
        closed_blockers=closed_blockers,
    )

def _desired_derived_labels(state: QueueState) -> tuple[bool, bool]:
    return state.reason == "ready", state.reason == "blocked"

def _update_derived_labels(
    repo: Path,
    github_repo: str,
    state: QueueState,
    *,
    runner: Callable[..., object] = subprocess.run,
) -> bool:
    labels = set(state.issue.labels)
    want_ready, want_blocked = _desired_derived_labels(state)
    add: list[str] = []
    remove: list[str] = []
    if want_ready and READY_LABEL not in labels:
        add.append(READY_LABEL)
    if not want_ready and READY_LABEL in labels:
        remove.append(READY_LABEL)
    if want_blocked and BLOCKED_LABEL not in labels:
        add.append(BLOCKED_LABEL)
    if not want_blocked and BLOCKED_LABEL in labels:
        remove.append(BLOCKED_LABEL)
    if not add and not remove:
        return False
    args = ["issue", "edit", str(state.issue.number), "--repo", github_repo]
    for name in add:
        args.extend(["--add-label", name])
    for name in remove:
        args.extend(["--remove-label", name])
    _run_gh(repo, args, runner=runner)
    return True
