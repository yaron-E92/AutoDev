from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable

from automation import development_policy, headless_environment
from automation.scheduler_types import SchedulerError


def _run_command(
    argv: list[str],
    *,
    runner: Callable[..., object] = subprocess.run,
    cwd: Path | None = None,
    input_text: str | None = None,
) -> object:
    kwargs: dict[str, object] = {
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "capture_output": True,
        "check": False,
        "env": headless_environment.environment(),
    }
    if cwd is not None:
        kwargs["cwd"] = cwd
    if input_text is not None:
        kwargs["input"] = input_text
    else:
        kwargs["stdin"] = subprocess.DEVNULL
    try:
        return runner(argv, **kwargs)
    except OSError as exc:
        raise SchedulerError(f"cannot execute {argv[0]}: {exc}") from exc


def _returncode(completed: object) -> int:
    return int(getattr(completed, "returncode", 1))


def _stdout(completed: object) -> str:
    return str(getattr(completed, "stdout", "") or "")


def _stderr(completed: object) -> str:
    return str(getattr(completed, "stderr", "") or "")


def _require_ok(completed: object, argv: list[str]) -> object:
    if _returncode(completed) != 0:
        detail = _stderr(completed).strip() or _stdout(completed).strip() or "no command output"
        raise SchedulerError(
            f"command failed ({_returncode(completed)}): {' '.join(argv)}: {detail}"
        )
    return completed


def _git(
    repo: Path,
    arguments: list[str],
    *,
    runner: Callable[..., object] = subprocess.run,
    check: bool = True,
) -> object:
    argv = ["git", "-C", str(repo), *arguments]
    completed = _run_command(argv, runner=runner)
    return _require_ok(completed, argv) if check else completed


def _git_status(repo: Path, *, runner: Callable[..., object] = subprocess.run) -> str:
    completed = _git(
        repo,
        ["status", "--porcelain", "--untracked-files=normal"],
        runner=runner,
    )
    return _stdout(completed).strip()


def _origin_url(repo: Path, *, runner: Callable[..., object] = subprocess.run) -> str:
    completed = _git(repo, ["remote", "get-url", "origin"], runner=runner)
    value = _stdout(completed).strip()
    if not value:
        raise SchedulerError(f"repository has no usable origin remote: {repo}")
    return value


def _github_default_branch(
    repo: Path,
    *,
    runner: Callable[..., object] = subprocess.run,
) -> str:
    symbolic = _git(
        repo,
        ["symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"],
        runner=runner,
        check=False,
    )
    if _returncode(symbolic) == 0:
        value = _stdout(symbolic).strip()
        if value.startswith("origin/") and len(value) > len("origin/"):
            return value[len("origin/") :]
    current = _git(repo, ["branch", "--show-current"], runner=runner)
    value = _stdout(current).strip()
    if not value:
        raise SchedulerError(f"cannot determine worker default branch: {repo}")
    return value


def _default_branch(repo: Path, *, runner: Callable[..., object] = subprocess.run) -> str:
    """Return AutoDev's normal-work branch for scheduler compatibility.

    The historical helper name is retained because registrations already call it,
    but Git-Flow repositories deliberately return their integration branch.
    """
    github_default = _github_default_branch(repo, runner=runner)
    try:
        return development_policy.normal_work_branch(
            repo,
            default_branch=github_default,
        )
    except development_policy.DevelopmentPolicyError as exc:
        raise SchedulerError(f"invalid repository development strategy: {exc}") from exc
