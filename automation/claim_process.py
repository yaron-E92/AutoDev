from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable, TextIO

from automation.claim_contract import (
    ClaimError,
)

def _run(
    repo: Path,
    argv: list[str],
    *,
    runner: Callable[..., object] = subprocess.run,
    input_text: str | None = None,
) -> object:
    kwargs: dict[str, object] = {
        "cwd": repo,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "capture_output": True,
        "check": False,
    }
    if input_text is not None:
        kwargs["input"] = input_text
    try:
        return runner(argv, **kwargs)
    except OSError as exc:
        raise ClaimError(f"cannot execute {argv[0]}: {exc}") from exc

def _returncode(completed: object) -> int:
    return int(getattr(completed, "returncode", 1))

def _stdout(completed: object) -> str:
    return str(getattr(completed, "stdout", "") or "")

def _stderr(completed: object) -> str:
    return str(getattr(completed, "stderr", "") or "")

def _require_ok(completed: object, argv: list[str]) -> object:
    if _returncode(completed) != 0:
        detail = _stderr(completed).strip() or _stdout(completed).strip() or "no command output"
        raise ClaimError(f"command failed ({_returncode(completed)}): {' '.join(argv)}: {detail}")
    return completed

def _git(
    repo: Path,
    args: list[str],
    *,
    runner: Callable[..., object] = subprocess.run,
    input_text: str | None = None,
    check: bool = True,
) -> object:
    argv = ["git", "-C", str(repo), *args]
    result = _run(repo, argv, runner=runner, input_text=input_text)
    return _require_ok(result, argv) if check else result

def _is_push_race(result: object) -> bool:
    if _returncode(result) == 0:
        return False
    text = (_stdout(result) + "\n" + _stderr(result)).casefold()
    markers = (
        "stale info",
        "non-fast-forward",
        "fetch first",
        "[rejected]",
        "cannot lock ref",
        "failed to push some refs",
        "remote ref does not exist",
    )
    return any(marker in text for marker in markers)
