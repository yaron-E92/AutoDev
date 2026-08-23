from __future__ import annotations

import os
import sys
from pathlib import Path

from automation import opencode_entrypoint, user_install


INTERACTIVE_CONSENT_ARG = "--interactive-consent"
INTERACTIVE_CONSENT_ENV = "AUTODEV_INTERACTIVE_CONSENT"
INTERACTIVE_CONSENT_VALUE = "controlling-terminal"


def _consume_interactive_consent_argument(values: list[str]) -> tuple[list[str], bool]:
    forwarded: list[str] = []
    interactive = False
    for value in values:
        if value == INTERACTIVE_CONSENT_ARG:
            interactive = True
            continue
        forwarded.append(value)
    return forwarded, interactive


def _enable_interactive_consent_for_direct_cli(*, explicit: bool = False) -> None:
    if explicit:
        os.environ[INTERACTIVE_CONSENT_ENV] = INTERACTIVE_CONSENT_VALUE
        return
    if os.environ.get("AUTODEV_HEADLESS", "").strip():
        return
    try:
        interactive = bool(sys.stdin.isatty() and sys.stdout.isatty())
    except AttributeError:
        interactive = False
    if interactive:
        os.environ.setdefault(INTERACTIVE_CONSENT_ENV, INTERACTIVE_CONSENT_VALUE)


def _help() -> str:
    return """AutoDev deterministic issue-to-PR automation.

Usage:
  autodev install --user [--add-to-path]
  autodev repo install [--no-opencode]
  autodev repo ensure-labels
  autodev repo doctor [--fix] [--json]
  autodev scheduler install [--backend auto|systemd-user|cron|windows-task]
  autodev scheduler status
  autodev scheduler health
  autodev scheduler notifications enable|disable|status
  autodev scheduler run-once
  autodev scheduler uninstall
  autodev status [existing status options]
  autodev coordinate [coordinator options]
  autodev resume [coordinator options]
  autodev privacy ...
  autodev queue ...

OpenCode slash commands are an optional frontend over this same Python core.
"""


def run(argv: list[str] | None = None) -> int:
    raw_values = list(sys.argv[1:] if argv is None else argv)
    values, explicit_interactive = _consume_interactive_consent_argument(raw_values)
    if not values or values[0] in {"-h", "--help", "help"}:
        print(_help(), end="")
        return 0

    command = values[0]
    rest = values[1:]
    if command == "install":
        return user_install.run_cli(rest, autodev_root=Path(__file__).resolve().parents[1])
    if command == "repo":
        from automation import repo_setup

        return repo_setup.run_cli(rest)
    if command == "scheduler":
        from automation import scheduler_health

        return scheduler_health.run_cli(rest)

    _enable_interactive_consent_for_direct_cli(explicit=explicit_interactive)
    if command == "resume":
        return opencode_entrypoint.run(["coordinate", "--resume", *rest])
    return opencode_entrypoint.run(values)


def main() -> int:
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
