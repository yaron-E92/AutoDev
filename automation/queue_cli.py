from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Callable, TextIO

from automation.queue_classification import (
    classify_issue,
)
from automation.queue_contract import (
    DEFAULT_LIMIT,
    MANAGED_LABEL,
    QueueError,
)
from automation.queue_github import (
    fetch_issue,
    list_blockers,
    resolve_github_repo,
)
from automation.queue_policy import (
    load_policy,
)
from automation.queue_presentation import (
    _state_json,
    explain_state,
    queue_summary,
)
from automation.queue_workflow import (
    inspect_queue,
    reconcile_queue,
)

def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="autodev queue",
        description="Maintain and select AutoDev issue queue state without model calls.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("reconcile", "status"):
        command = sub.add_parser(name)
        command.add_argument("--github-repo", default="")
        command.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
        command.add_argument("--json", action="store_true")
    explain = sub.add_parser("explain")
    explain.add_argument("issue", type=int)
    explain.add_argument("--github-repo", default="")
    explain.add_argument("--json", action="store_true")
    next_command = sub.add_parser(
        "next",
        help="Select one next autonomous issue or surface the existing run.",
    )
    next_command.add_argument("--github-repo", default="")
    next_command.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    next_command.add_argument("--dry-run", action="store_true")
    next_command.add_argument("--json", action="store_true")
    return parser

def run_cli(
    argv: list[str] | None = None,
    *,
    repo: Path | None = None,
    runner: Callable[..., object] = subprocess.run,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    out = stdout or sys.stdout
    err = stderr or sys.stderr
    args = _parser().parse_args(argv)
    current = (repo or Path.cwd()).expanduser().resolve()
    try:
        github_repo = resolve_github_repo(
            current,
            explicit=str(getattr(args, "github_repo", "")),
            runner=runner,
        )
        if args.command == "reconcile":
            states, created = reconcile_queue(
                current,
                github_repo,
                limit=args.limit,
                runner=runner,
            )
            if args.json:
                print(
                    json.dumps(
                        {
                            "repository": github_repo,
                            "summary": queue_summary(states),
                            "created_labels": list(created),
                            "changed_issues": [
                                state.issue.number for state in states if state.changed
                            ],
                            "closed_dependencies_removed": {
                                str(state.issue.number): list(
                                    state.removed_closed_dependencies
                                )
                                for state in states
                                if state.removed_closed_dependencies
                            },
                        },
                        sort_keys=True,
                    ),
                    file=out,
                )
            else:
                summary = queue_summary(states)
                changed = sum(state.changed for state in states)
                removed = sum(
                    len(state.removed_closed_dependencies) for state in states
                )
                print(
                    f"AutoDev queue reconciled for {github_repo}: "
                    f"managed={summary['managed']} ready={summary['ready']} "
                    f"blocked={summary['dependency_blocked']} "
                    f"attention={summary['attention_required']} "
                    f"changed={changed} closed-dependencies-removed={removed}",
                    file=out,
                )
            return 0
        if args.command == "status":
            states = inspect_queue(
                current,
                github_repo,
                limit=args.limit,
                runner=runner,
            )
            summary = queue_summary(states)
            if args.json:
                print(
                    json.dumps(
                        {"repository": github_repo, "summary": summary},
                        sort_keys=True,
                    ),
                    file=out,
                )
            else:
                print(
                    f"AutoDev queue status for {github_repo}: "
                    f"managed={summary['managed']} ready={summary['ready']} "
                    f"blocked={summary['dependency_blocked']} "
                    f"attention={summary['attention_required']} "
                    f"running={summary['running']} "
                    f"policy-excluded={summary['policy_excluded']}",
                    file=out,
                )
            return 0
        if args.command == "next":
            from automation import queue_selection

            result = queue_selection.select_next(
                current,
                github_repo,
                limit=args.limit,
                dry_run=bool(args.dry_run),
                runner=runner,
            )
            if args.json:
                print(json.dumps(result.to_json(), sort_keys=True), file=out)
            else:
                print(queue_selection.render_selection(result), file=out)
            return 0

        issue = fetch_issue(current, github_repo, args.issue, runner=runner)
        blockers = (
            list_blockers(current, github_repo, issue.number, runner=runner)
            if issue.state == "open" and MANAGED_LABEL in issue.labels
            else []
        )
        state = classify_issue(issue, blockers, load_policy(current))
        if args.json:
            print(json.dumps(_state_json(state), sort_keys=True), file=out)
        else:
            print(explain_state(state), file=out)
        return 0
    except QueueError as exc:
        print(str(exc), file=err)
        return 2
