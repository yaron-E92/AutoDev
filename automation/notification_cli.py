from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Callable, TextIO

from automation import notification_storage, repository_identity
from automation.notification_contract import (
    NOTIFICATION_NATIVE,
    NotificationError,
    NotificationPolicy,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="autodev notifications")
    sub = parser.add_subparsers(dest="action", required=True)
    for name in ("enable", "disable", "status"):
        command = sub.add_parser(name)
        command.add_argument("--repo", default=".")
        command.add_argument("--github-repo", default="")
        command.add_argument("--json", action="store_true")
        if name == "enable":
            command.add_argument("--reminder-hours", type=int, default=0)
    return parser


def run_cli(
    argv: list[str],
    *,
    home: Path | None = None,
    runner: Callable[..., object] = subprocess.run,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    args = _parser().parse_args(argv)
    repo = Path(args.repo).expanduser().resolve()
    try:
        repository = repository_identity.resolve_github_repository(
            repo,
            explicit=args.github_repo,
            runner=runner,
        )
        if args.action == "enable":
            policy = NotificationPolicy(
                backend=NOTIFICATION_NATIVE,
                reminder_hours=args.reminder_hours,
            )
            notification_storage.save_policy(repository, policy, home=home)
        elif args.action == "disable":
            policy = NotificationPolicy()
            notification_storage.save_policy(repository, policy, home=home)
        else:
            policy = notification_storage.load_policy(repository, home=home)
        state = notification_storage.load_event_state_path(
            notification_storage.event_state_path(repository, home=home)
        )
    except (NotificationError, repository_identity.RepositoryIdentityError) as exc:
        print(str(exc), file=stderr)
        return 2

    if args.json:
        print(
            json.dumps(
                {
                    "repository": repository,
                    "policy": policy.to_json(),
                    "events": state.get("modes", {}),
                },
                sort_keys=True,
            ),
            file=stdout,
        )
    else:
        print(
            f"Notifications for {repository}: {policy.backend}"
            + (
                f"; blocked/attention reminder every {policy.reminder_hours}h"
                if policy.reminder_hours
                else ""
            ),
            file=stdout,
        )
        print("Modes: manual, scheduled", file=stdout)
        print("Events: ready-for-review, blocked/attention-required, failed", file=stdout)
    return 0
