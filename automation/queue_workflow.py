from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable, TextIO

from automation.queue_classification import (
    _update_derived_labels,
    classify_issue,
)
from automation.queue_contract import (
    ATTENTION_LABEL,
    BLOCKED_LABEL,
    Blocker,
    DEFAULT_LIMIT,
    MANAGED_LABEL,
    QueueState,
    READY_LABEL,
    RUNNING_LABEL,
)
from automation.queue_github import (
    ensure_queue_labels,
    list_blockers,
    list_issues,
    remove_dependency,
)
from automation.queue_policy import (
    load_policy,
)

def inspect_queue(
    repo: Path,
    github_repo: str,
    *,
    limit: int = DEFAULT_LIMIT,
    runner: Callable[..., object] = subprocess.run,
) -> list[QueueState]:
    repo = repo.expanduser().resolve()
    policy = load_policy(repo)
    states: list[QueueState] = []
    for issue in list_issues(repo, github_repo, limit=limit, runner=runner):
        labels = set(issue.labels)
        if not labels.intersection(
            {
                MANAGED_LABEL,
                READY_LABEL,
                BLOCKED_LABEL,
                ATTENTION_LABEL,
                RUNNING_LABEL,
            }
        ):
            continue
        blockers: list[Blocker] = []
        if issue.state == "open" and MANAGED_LABEL in labels:
            blockers = list_blockers(repo, github_repo, issue.number, runner=runner)
        states.append(classify_issue(issue, blockers, policy))
    return states

def reconcile_queue(
    repo: Path,
    github_repo: str,
    *,
    limit: int = DEFAULT_LIMIT,
    runner: Callable[..., object] = subprocess.run,
) -> tuple[list[QueueState], tuple[str, ...]]:
    repo = repo.expanduser().resolve()
    created_labels = ensure_queue_labels(repo, github_repo, runner=runner)
    policy = load_policy(repo)
    states: list[QueueState] = []
    for issue in list_issues(repo, github_repo, limit=limit, runner=runner):
        labels = set(issue.labels)
        if not labels.intersection(
            {
                MANAGED_LABEL,
                READY_LABEL,
                BLOCKED_LABEL,
                ATTENTION_LABEL,
                RUNNING_LABEL,
            }
        ):
            continue
        blockers: list[Blocker] = []
        removed: list[int] = []
        if issue.state == "open" and MANAGED_LABEL in labels:
            blockers = list_blockers(repo, github_repo, issue.number, runner=runner)
            for blocker in blockers:
                if blocker.state == "open":
                    continue
                remove_dependency(
                    repo,
                    github_repo,
                    issue.number,
                    blocker.id,
                    runner=runner,
                )
                removed.append(blocker.number)
            blockers = [item for item in blockers if item.state == "open"]
        state = classify_issue(issue, blockers, policy)
        changed = _update_derived_labels(repo, github_repo, state, runner=runner)
        states.append(
            QueueState(
                issue=state.issue,
                reason=state.reason,
                open_blockers=state.open_blockers,
                closed_blockers=state.closed_blockers,
                changed=changed,
                removed_closed_dependencies=tuple(sorted(removed)),
            )
        )
    return states, created_labels
