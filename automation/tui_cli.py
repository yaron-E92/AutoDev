from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Callable, TextIO

from automation import cli_help, tui_model, tui_terminal


DEFAULT_REMOTE_REFRESH_SECONDS = 30.0
MIN_REMOTE_REFRESH_SECONDS = 10.0


def register_help() -> None:
    cli_help.HELP.setdefault(
        ("tui",),
        cli_help.HelpEntry(
            usage="autodev tui [options]",
            summary="Interactively supervise authoritative AutoDev state in one terminal view.",
            description=(
                "The TUI is a read-mostly operator view over AutoDev's existing queue, run, scheduler, "
                "privacy, and notification contracts. It does not implement a second workflow engine. "
                "Remote queue refresh is bounded and model-free; guarded mutations require confirmation."
            ),
            options=cli_help.COMMON_LOCATION_OPTIONS
            + (
                ("--refresh-seconds N", "Remote GitHub refresh interval. Minimum/default: 10/30 seconds."),
                ("--once", "Render one terminal-independent snapshot and exit."),
                ("--json", "With --once, emit the snapshot as JSON instead of text."),
            ),
            examples=(
                "autodev tui",
                "autodev tui --refresh-seconds 60",
                "autodev tui --once",
                "autodev tui --once --json",
            ),
        ),
    )
    cli_help.KNOWN_TOP_LEVEL.add("tui")
    groups = []
    for title, rows in cli_help.TOP_LEVEL_GROUPS:
        if title == "Automation and operations" and not any(name == "tui" for name, _ in rows):
            rows = (("tui", "Supervise AutoDev queue/run/scheduler/privacy state interactively."), *rows)
        groups.append((title, rows))
    cli_help.TOP_LEVEL_GROUPS = tuple(groups)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="autodev tui")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--github-repo", default="")
    parser.add_argument("--refresh-seconds", type=float, default=DEFAULT_REMOTE_REFRESH_SECONDS)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser


def _json(snapshot: tui_model.TuiSnapshot) -> dict[str, object]:
    return {
        "repository": snapshot.repository,
        "repo_path": snapshot.repo_path,
        "observed_at": snapshot.observed_at,
        "remote_observed_at": snapshot.remote_observed_at,
        "queue": dict(snapshot.queue),
        "active_claims": snapshot.active_claims,
        "selected_issue_number": snapshot.selected_issue_number,
        "issues": [
            {
                "number": issue.number,
                "title": issue.title,
                "url": issue.url,
                "queue_state": issue.queue_state,
                "blockers": list(issue.blockers),
            }
            for issue in snapshot.issues
        ],
        "run": snapshot.run.__dict__,
        "scheduler": snapshot.scheduler.__dict__,
        "privacy": snapshot.privacy.__dict__,
        "remote_error": snapshot.remote_error,
    }


def run_cli(
    argv: list[str] | None = None,
    *,
    runner: Callable[..., object] = subprocess.run,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    args = _parser().parse_args(argv)
    if args.refresh_seconds < MIN_REMOTE_REFRESH_SECONDS:
        print(
            f"autodev tui: --refresh-seconds must be at least {MIN_REMOTE_REFRESH_SECONDS:g}",
            file=stderr,
        )
        return 2
    if args.json and not args.once:
        print("autodev tui: --json requires --once", file=stderr)
        return 2

    try:
        repo, identity = tui_model.resolve_repository(
            Path(args.repo), github_repo=args.github_repo, runner=runner
        )
        snapshot = tui_model.collect_local(repo, identity, runner=runner)
        snapshot = tui_model.collect_remote(snapshot, runner=runner)
    except Exception as exc:
        print(f"autodev tui: {exc}", file=stderr)
        return 2

    if args.once:
        if args.json:
            print(json.dumps(_json(snapshot), sort_keys=True), file=stdout)
        else:
            print(
                tui_terminal.render(
                    snapshot,
                    tui_terminal.ViewState(),
                    width=100,
                    height=40,
                ),
                file=stdout,
            )
        return 0

    try:
        return tui_terminal.run_interactive(
            snapshot,
            remote_refresh_seconds=args.refresh_seconds,
            stdout=stdout,
            refresh_local=lambda value: tui_model.refresh_local(value, runner=runner),
            refresh_remote=lambda value: tui_model.collect_remote(value, runner=runner),
        )
    except Exception as exc:
        print(f"autodev tui: {exc}", file=stderr)
        return 2


def main() -> int:
    return run_cli()


if __name__ == "__main__":
    raise SystemExit(main())
