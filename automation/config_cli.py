from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TextIO

from automation import user_config
from automation.scheduler_types import (
    DEFAULT_CADENCE_MINUTES,
    MAX_CADENCE_MINUTES,
    MIN_CADENCE_MINUTES,
)


def _repository(path: str) -> str:
    identity = user_config.repository_identity(Path(path))
    if not identity:
        raise user_config.UserConfigError(
            f"cannot resolve a GitHub OWNER/REPO identity from {Path(path).expanduser().resolve()}"
        )
    return identity


def _assignments(values: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        role, separator, model = value.partition("=")
        if not separator or not role.strip() or not model.strip():
            raise user_config.UserConfigError(
                f"profile assignment {value!r} must use ROLE=PROVIDER/MODEL syntax"
            )
        result[role.strip()] = model.strip()
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="autodev config")
    sub = parser.add_subparsers(dest="command", required=True)

    show = sub.add_parser("show")
    show.add_argument("--json", action="store_true")

    sub.add_parser("path")

    profile = sub.add_parser("profile")
    profile_sub = profile.add_subparsers(dest="profile_command", required=True)
    profile_sub.add_parser("list")
    set_profile = profile_sub.add_parser("set")
    set_profile.add_argument("name")
    set_profile.add_argument("assignments", nargs="+")
    use_profile = profile_sub.add_parser("use")
    use_profile.add_argument("name")
    use_profile.add_argument("--repo", default="")
    clear_profile = profile_sub.add_parser("clear")
    clear_profile.add_argument("--repo", default="")

    cadence = sub.add_parser("scheduler-cadence")
    cadence.add_argument("minutes", nargs="?", type=int)
    return parser


def run_cli(
    argv: list[str] | None = None,
    *,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    args = build_parser().parse_args(argv)
    try:
        path = user_config.config_path()
        if args.command == "path":
            if path is None:
                raise user_config.UserConfigError("cannot determine the AutoDev user configuration path")
            print(path, file=stdout)
            return 0

        value = user_config.load(path)
        if args.command == "show":
            payload = {"path": str(path) if path is not None else "", "config": value}
            print(
                json.dumps(payload if args.json else value, indent=2, sort_keys=True),
                file=stdout,
            )
            return 0

        if args.command == "profile":
            if args.profile_command == "list":
                profiles = value.get("model_profiles", {})
                active = str(value.get("active_model_profile", "") or "")
                if isinstance(profiles, dict):
                    for name in sorted(map(str, profiles)):
                        print(f"{'*' if name == active else ' '} {name}", file=stdout)
                return 0
            if args.profile_command == "set":
                value = user_config.set_model_profile(
                    value,
                    args.name,
                    _assignments(args.assignments),
                )
                written = user_config.save(value, path)
                print(f"Saved model profile {args.name!r} in {written}.", file=stdout)
                return 0
            repository = _repository(args.repo) if args.repo else ""
            if args.profile_command == "use":
                value = user_config.select_profile(value, args.name, repository=repository)
                written = user_config.save(value, path)
                scope = repository or "user default"
                print(f"Selected model profile {args.name!r} for {scope} in {written}.", file=stdout)
                return 0
            if args.profile_command == "clear":
                value = user_config.clear_profile_selection(value, repository=repository)
                written = user_config.save(value, path)
                scope = repository or "user default"
                print(f"Cleared model profile selection for {scope} in {written}.", file=stdout)
                return 0

        if args.command == "scheduler-cadence":
            if args.minutes is None:
                configured = user_config.scheduler_cadence(value)
                print(configured if configured is not None else DEFAULT_CADENCE_MINUTES, file=stdout)
                return 0
            if not MIN_CADENCE_MINUTES <= args.minutes <= MAX_CADENCE_MINUTES:
                raise user_config.UserConfigError(
                    f"scheduler cadence must be between {MIN_CADENCE_MINUTES} and {MAX_CADENCE_MINUTES} minutes"
                )
            value = user_config.set_scheduler_cadence(value, args.minutes)
            written = user_config.save(value, path)
            print(f"Set default scheduler cadence to {args.minutes} minute(s) in {written}.", file=stdout)
            return 0
    except user_config.UserConfigError as exc:
        print(f"autodev config: {exc}", file=stderr)
        return 2
    return 2
