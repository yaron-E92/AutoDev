from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable, TextIO
from automation import issue_queue, privacy, privacy_grants, queue_selection, scheduler, workflow_stages

from automation.scheduler_health_contract import (
    NOTIFICATION_NATIVE,
    NotificationPolicy,
    SchedulerHealthError,
)
from automation.scheduler_health_lifecycle import (
    _location_parser,
    _resolve_registration,
    current_health,
    run_tick,
)
from automation.scheduler_health_notifications import (
    observe_health,
)
from automation.scheduler_health_probes import (
    render_health,
)
from automation.scheduler_health_storage import (
    health_path,
    load_notification_policy,
    notification_path,
    save_notification_policy,
)

def run_status(
    argv: list[str],
    *,
    home: Path | None = None,
    runner: Callable[..., object] = subprocess.run,
    which: Callable[[str], str | None] = shutil.which,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    args = _location_parser("autodev scheduler status").parse_args(argv[1:] if argv and argv[0] == "status" else argv)
    try:
        registration_file, registration = _resolve_registration(
            repo=Path(args.repo),
            github_repo=args.github_repo,
            registration=args.registration,
            home=home,
            runner=runner,
        )
        status = scheduler.scheduler_status(
            Path(args.repo),
            github_repo=args.github_repo,
            registration_file=registration_file,
            home=home,
            runner=runner,
        )
        snapshot = current_health(registration_file, registration, runner=runner, which=which)
        policy = load_notification_policy(registration_file)
        observe_health(registration_file, snapshot, policy=NotificationPolicy())
    except (SchedulerHealthError, scheduler.SchedulerError, issue_queue.QueueError, privacy.PrivacyError) as exc:
        print(str(exc), file=stderr)
        return 2
    if args.json:
        print(
            json.dumps(
                {
                    "scheduler": status.to_json(),
                    "health": snapshot.to_json(),
                    "notifications": policy.to_json(),
                },
                sort_keys=True,
            ),
            file=stdout,
        )
    else:
        print(scheduler._render_status(status), file=stdout)  # type: ignore[attr-defined]
        print(render_health(snapshot), file=stdout)
        print(
            f"Notifications: {policy.backend}"
            + (f"; reminder={policy.reminder_hours}h" if policy.reminder_hours else ""),
            file=stdout,
        )
    return 2 if status.state == "NEEDS_ATTENTION" or snapshot.state == "SCHEDULER_ERROR" else 0

def run_health(
    argv: list[str],
    *,
    home: Path | None = None,
    runner: Callable[..., object] = subprocess.run,
    which: Callable[[str], str | None] = shutil.which,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    args = _location_parser("autodev scheduler health").parse_args(argv[1:] if argv and argv[0] == "health" else argv)
    try:
        registration_file, registration = _resolve_registration(
            repo=Path(args.repo),
            github_repo=args.github_repo,
            registration=args.registration,
            home=home,
            runner=runner,
        )
        snapshot = current_health(registration_file, registration, runner=runner, which=which)
        observe_health(registration_file, snapshot, policy=NotificationPolicy())
    except (SchedulerHealthError, scheduler.SchedulerError, issue_queue.QueueError, privacy.PrivacyError) as exc:
        print(str(exc), file=stderr)
        return 2
    print(json.dumps(snapshot.to_json(), sort_keys=True) if args.json else render_health(snapshot), file=stdout)
    return 2 if snapshot.state == "SCHEDULER_ERROR" else 0

def run_notifications(
    argv: list[str],
    *,
    home: Path | None = None,
    runner: Callable[..., object] = subprocess.run,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    parser = argparse.ArgumentParser(prog="autodev scheduler notifications")
    sub = parser.add_subparsers(dest="action", required=True)
    for name in ("enable", "disable", "status"):
        command = sub.add_parser(name)
        command.add_argument("--repo", default=".")
        command.add_argument("--github-repo", default="")
        command.add_argument("--registration", default="")
        command.add_argument("--json", action="store_true")
        if name == "enable":
            command.add_argument("--reminder-hours", type=int, default=0)
    args = parser.parse_args(argv[1:] if argv and argv[0] == "notifications" else argv)
    try:
        registration_file, _registration = _resolve_registration(
            repo=Path(args.repo),
            github_repo=args.github_repo,
            registration=args.registration,
            home=home,
            runner=runner,
        )
        if args.action == "enable":
            policy = NotificationPolicy(
                backend=NOTIFICATION_NATIVE,
                reminder_hours=args.reminder_hours,
            )
            save_notification_policy(registration_file, policy)
        elif args.action == "disable":
            policy = NotificationPolicy()
            save_notification_policy(registration_file, policy)
        else:
            policy = load_notification_policy(registration_file)
    except (SchedulerHealthError, scheduler.SchedulerError, issue_queue.QueueError) as exc:
        print(str(exc), file=stderr)
        return 2
    if args.json:
        print(json.dumps(policy.to_json(), sort_keys=True), file=stdout)
    else:
        print(
            f"Scheduler notifications: {policy.backend}"
            + (f"; attention reminder every {policy.reminder_hours}h" if policy.reminder_hours else ""),
            file=stdout,
        )
    return 0

def _cleanup_health_state(argv: list[str], *, home: Path | None, runner: Callable[..., object]) -> None:
    try:
        args, _ = _location_parser("autodev scheduler uninstall").parse_known_args(argv[1:])
        registration_file, _registration = _resolve_registration(
            repo=Path(args.repo),
            github_repo=args.github_repo,
            registration=args.registration,
            home=home,
            runner=runner,
        )
    except Exception:
        return
    for path in (health_path(registration_file), notification_path(registration_file)):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

def run_cli(
    argv: list[str] | None = None,
    *,
    home: Path | None = None,
    runner: Callable[..., object] = subprocess.run,
    which: Callable[[str], str | None] = shutil.which,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    values = list(argv or [])
    if not values:
        return scheduler.run_cli(values, home=home, runner=runner, which=which, stdout=stdout, stderr=stderr)
    command = values[0]
    if command == "run-once":
        return run_tick(values, home=home, runner=runner, which=which, stdout=stdout, stderr=stderr)
    if command == "status":
        return run_status(values, home=home, runner=runner, which=which, stdout=stdout, stderr=stderr)
    if command == "health":
        return run_health(values, home=home, runner=runner, which=which, stdout=stdout, stderr=stderr)
    if command == "notifications":
        return run_notifications(values, home=home, runner=runner, stdout=stdout, stderr=stderr)
    if command == "uninstall":
        _cleanup_health_state(values, home=home, runner=runner)
    return scheduler.run_cli(values, home=home, runner=runner, which=which, stdout=stdout, stderr=stderr)
