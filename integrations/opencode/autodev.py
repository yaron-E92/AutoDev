from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Callable


CONFIG_VERSION = 1


def _current_issue_number() -> int:
    state_path = Path.cwd() / ".autodev-run" / "current" / "state.json"
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


def _github_identity_from_remote(remote_url: str) -> tuple[str, str] | None:
    value = remote_url.strip()
    prefixes = (
        "https://github.com/",
        "http://github.com/",
        "ssh://git@github.com/",
        "git@github.com:",
    )
    path = next((value[len(prefix) :] for prefix in prefixes if value.startswith(prefix)), "")
    if not path:
        return None
    path = path.rstrip("/")
    if path.endswith(".git"):
        path = path[:-4]
    parts = path.split("/")
    if len(parts) != 2 or not all(parts):
        return None
    return parts[0], parts[1]


def _resolve_github_environment(
    env: dict[str, str],
    repo: Path,
    *,
    runner: Callable[..., object] = subprocess.run,
) -> None:
    if env.get("GITHUB_OWNER", "").strip() and env.get("GITHUB_REPO", "").strip():
        return

    remote_name = env.get("REMOTE_NAME", "").strip() or "origin"
    try:
        completed = runner(
            ["git", "remote", "get-url", remote_name],
            cwd=repo,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
        )
    except OSError:
        return
    if int(getattr(completed, "returncode", 1)) != 0:
        return

    identity = _github_identity_from_remote(str(getattr(completed, "stdout", "") or ""))
    if identity is None:
        return
    owner, repo_name = identity
    if not env.get("GITHUB_OWNER", "").strip():
        env["GITHUB_OWNER"] = owner
    if not env.get("GITHUB_REPO", "").strip():
        env["GITHUB_REPO"] = repo_name


def _bridge_environment(
    python: str,
    autodev_root: Path,
    repo: Path,
    *,
    runner: Callable[..., object] = subprocess.run,
) -> dict[str, str]:
    env = dict(os.environ)
    old_python_path = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        str(autodev_root)
        if not old_python_path
        else str(autodev_root) + os.pathsep + old_python_path
    )
    env["AUTODEV_PYTHON"] = python
    _resolve_github_environment(env, repo, runner=runner)
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


def _load_config(config_path: Path) -> tuple[Path, str]:
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read valid JSON: {exc}") from exc
    if not isinstance(config, dict):
        raise ValueError("top-level value must be an object")
    if config.get("version") != CONFIG_VERSION:
        raise ValueError(f"version must be {CONFIG_VERSION}")

    raw_root = config.get("autodev_root")
    if not isinstance(raw_root, str) or not raw_root.strip():
        raise ValueError("required field 'autodev_root' must be a non-empty string")
    autodev_root = Path(raw_root).expanduser()
    if not autodev_root.is_dir():
        raise ValueError(f"configured AutoDev root does not exist: {autodev_root}")

    raw_python = config.get("python")
    if not isinstance(raw_python, str) or not raw_python.strip():
        raise ValueError("required field 'python' must be a non-empty string")
    return autodev_root, raw_python.strip()


def main() -> int:
    root = Path(__file__).resolve().parent
    config_path = root / "autodev.json"
    try:
        autodev_root, python = _load_config(config_path)
    except ValueError as exc:
        print(f"Invalid AutoDev OpenCode configuration: {config_path}: {exc}", file=sys.stderr)
        return 1

    repo = Path.cwd()
    completed = subprocess.run(
        [
            python,
            "-m",
            "automation.opencode_runtime",
            *_arguments_with_current_issue(sys.argv[1:]),
        ],
        cwd=repo,
        env=_bridge_environment(python, autodev_root, repo),
        check=False,
    )
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
