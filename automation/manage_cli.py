from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable, TextIO

from automation import cli_help
from automation.queue_contract import DEFAULT_LIMIT, MANAGED_LABEL, QueueError, QueueIssue
from automation.queue_github import (
    add_issue_label,
    ensure_queue_labels,
    fetch_issue,
    list_issues,
    resolve_github_repo,
)


ISSUE_RE = re.compile(r"^#?([1-9][0-9]*)$")


def register_help() -> None:
    """Register the deterministic manage surface with the shared CLI help catalog."""
    cli_help.HELP.setdefault(
        ("manage",),
        cli_help.HelpEntry(
            usage="autodev manage (ISSUE | --all | --list) [options]",
            summary="Opt open GitHub issues into AutoDev management without starting work.",
            description=(
                "`autodev:managed` is operator authorization, not readiness. This command never "
                "adds `autodev:ready`, changes dependency relationships, or starts an issue-to-PR run. "
                "`--list` is strictly read-only."
            ),
            arguments=(("ISSUE", "Open issue number; accepts 123 or a quoted '#123'."),),
            options=cli_help.COMMON_LOCATION_OPTIONS
            + (
                ("--all", "Add `autodev:managed` to every open issue; pull requests are excluded."),
                ("--list", "List open managed issues without mutation."),
                ("--json", "Emit stable machine-readable output."),
            ),
            examples=(
                "autodev manage 123",
                "autodev manage '#123'",
                "autodev manage --all",
                "autodev manage --list",
                "autodev manage --list --json",
            ),
        ),
    )
    cli_help.KNOWN_TOP_LEVEL.add("manage")
    groups: list[tuple[str, tuple[tuple[str, str], ...]]] = []
    for title, rows in cli_help.TOP_LEVEL_GROUPS:
        if title == "Automation and operations" and not any(
            name == "manage" for name, _description in rows
        ):
            rows = (
                ("manage", "Opt open GitHub issues into management without starting work."),
                *rows,
            )
        groups.append((title, rows))
    cli_help.TOP_LEVEL_GROUPS = tuple(groups)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="autodev manage",
        description=(
            "Opt open GitHub issues into AutoDev management without making them ready or starting a run."
        ),
    )
    parser.add_argument("issue", nargs="?", help="Open issue number, with optional leading #.")
    parser.add_argument("--all", dest="all_issues", action="store_true", help="Manage every open issue.")
    parser.add_argument("--list", dest="list_managed", action="store_true", help="List open managed issues without mutation.")
    parser.add_argument("--repo", default=".", help="Repository root. Default: current directory.")
    parser.add_argument("--github-repo", default="", help="GitHub repository as owner/name. Default: detect from Git remote.")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help=argparse.SUPPRESS)
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    return parser


def _repository(value: str) -> Path:
    repo = Path(value).expanduser().resolve()
    if not repo.is_dir():
        raise QueueError(f"repository directory does not exist: {repo}")
    if not (repo / ".git").exists():
        raise QueueError(f"not a Git repository root: {repo}")
    return repo


def _issue_number(raw: str) -> int:
    match = ISSUE_RE.fullmatch(raw.strip())
    if not match:
        raise QueueError(f"ISSUE must be a positive integer, optionally prefixed with '#', got {raw!r}")
    return int(match.group(1))


def _issue_json(issue: QueueIssue, *, managed: bool | None = None) -> dict[str, object]:
    labels = set(issue.labels)
    if managed is True:
        labels.add(MANAGED_LABEL)
    return {
        "number": issue.number,
        "title": issue.title,
        "url": issue.url,
        "labels": sorted(labels),
    }


def _selection(args: argparse.Namespace) -> str:
    selected = int(bool(args.issue)) + int(bool(args.all_issues)) + int(bool(args.list_managed))
    if selected != 1:
        raise QueueError("choose exactly one of ISSUE, --all, or --list")
    if args.issue:
        return "single"
    if args.all_issues:
        return "all"
    return "list"


def _open_managed(issues: list[QueueIssue]) -> list[QueueIssue]:
    return [issue for issue in issues if issue.state == "open" and MANAGED_LABEL in issue.labels]


def run_cli(
    argv: list[str] | None = None,
    *,
    runner: Callable[..., object] = subprocess.run,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    out = stdout or sys.stdout
    err = stderr or sys.stderr
    args = _parser().parse_args(argv)
    try:
        mode = _selection(args)
        if args.limit <= 0:
            raise QueueError("--limit must be a positive integer")
        repo = _repository(args.repo)
        github_repo = resolve_github_repo(repo, explicit=args.github_repo, runner=runner)

        if mode == "list":
            managed = _open_managed(list_issues(repo, github_repo, limit=args.limit, runner=runner))
            if args.json:
                print(
                    json.dumps(
                        {
                            "repository": github_repo,
                            "mode": "list",
                            "count": len(managed),
                            "issues": [_issue_json(issue) for issue in managed],
                        },
                        sort_keys=True,
                    ),
                    file=out,
                )
            else:
                print(f"Managed open issues for {github_repo}: {len(managed)}", file=out)
                for issue in managed:
                    print(f"  #{issue.number} {issue.title}", file=out)
            return 0

        # Mutating manage operations use the same canonical label bootstrap as
        # `autodev repo ensure-labels`. This creates label definitions only; it
        # does not apply derived `ready` state to any issue.
        ensure_queue_labels(repo, github_repo, runner=runner)

        if mode == "single":
            number = _issue_number(args.issue)
            issue = fetch_issue(repo, github_repo, number, runner=runner)
            if issue.state != "open":
                raise QueueError(f"issue #{number} is not open")
            already = MANAGED_LABEL in issue.labels
            if not already:
                add_issue_label(repo, github_repo, number, MANAGED_LABEL, runner=runner)
            if args.json:
                print(
                    json.dumps(
                        {
                            "repository": github_repo,
                            "mode": "single",
                            "issue": _issue_json(issue, managed=True),
                            "newly_managed": 0 if already else 1,
                            "already_managed": 1 if already else 0,
                        },
                        sort_keys=True,
                    ),
                    file=out,
                )
            elif already:
                print(f"Issue #{number} is already managed: {issue.title}", file=out)
            else:
                print(f"Managed issue #{number}: {issue.title}", file=out)
            return 0

        open_issues = [
            issue
            for issue in list_issues(repo, github_repo, limit=args.limit, runner=runner)
            if issue.state == "open"
        ]
        already_managed = 0
        newly_managed = 0
        for issue in open_issues:
            if MANAGED_LABEL in issue.labels:
                already_managed += 1
                continue
            add_issue_label(repo, github_repo, issue.number, MANAGED_LABEL, runner=runner)
            newly_managed += 1
        if args.json:
            print(
                json.dumps(
                    {
                        "repository": github_repo,
                        "mode": "all",
                        "newly_managed": newly_managed,
                        "already_managed": already_managed,
                        "open_issues": len(open_issues),
                    },
                    sort_keys=True,
                ),
                file=out,
            )
        else:
            print(
                f"AutoDev management updated for {github_repo}: "
                f"newly-managed={newly_managed} already-managed={already_managed}",
                file=out,
            )
        return 0
    except QueueError as exc:
        print(f"autodev manage: {exc}", file=err)
        return 2


def main() -> int:
    return run_cli()


if __name__ == "__main__":
    raise SystemExit(main())
