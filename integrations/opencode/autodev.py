from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path


def _current_issue_number() -> int:
    state_path = Path.cwd() / ".codex-run" / "current" / "state.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    return int(state.get("IssueNumber", 0) or 0) if isinstance(state, dict) else 0


def _arguments_with_current_issue(arguments: list[str]) -> list[str]:
    if not arguments or arguments[0] != "prepare":
        return arguments
    issue_number = _current_issue_number()
    if issue_number <= 0:
        return arguments

    if "--arguments" not in arguments:
        return [*arguments, "--arguments", str(issue_number)]

    index = arguments.index("--arguments")
    if index + 1 >= len(arguments):
        return arguments
    value = arguments[index + 1]
    if re.search(r"(?<!\d)#?\d+(?!\d)", value):
        return arguments
    updated = list(arguments)
    updated[index + 1] = f"{issue_number} {value}".strip()
    return updated


def _bridge_environment(python: str, autodev_root: Path) -> dict[str, str]:
    env = dict(os.environ)
    old_python_path = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        str(autodev_root)
        if not old_python_path
        else str(autodev_root) + os.pathsep + old_python_path
    )
    if (
        os.name != "nt"
        and not env.get("LOCAL_CHECK", "").strip()
        and not env.get("PROFILES_PATH", "").strip()
    ):
        env["LOCAL_CHECK"] = (
            f"{shlex.quote(python)} -m automation.workflow_verify_current "
            f"--autodev-root {shlex.quote(str(autodev_root))}"
        )
    return env


def main() -> int:
    root = Path(__file__).resolve().parent
    config_path = root / "autodev.json"
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Invalid AutoDev OpenCode configuration: {config_path}: {exc}", file=sys.stderr)
        return 1

    autodev_root = Path(str(config.get("autodev_root", ""))).expanduser()
    if not autodev_root.is_dir():
        print(f"Configured AutoDev root does not exist: {autodev_root}", file=sys.stderr)
        return 1

    python = str(config.get("python", "")).strip() or sys.executable
    completed = subprocess.run(
        [
            python,
            "-m",
            "automation.opencode_adapter",
            *_arguments_with_current_issue(sys.argv[1:]),
        ],
        cwd=Path.cwd(),
        env=_bridge_environment(python, autodev_root),
        check=False,
    )
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
