from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from automation.windows_verification_contract import (
    WindowsVerificationError,
)

def _run(
    runner: Callable[..., object],
    command: list[str],
    *,
    cwd: Path,
    timeout: int | None = None,
) -> object:
    return runner(
        command,
        cwd=cwd,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
        timeout=timeout,
    )

def _stdout(completed: object) -> str:
    return str(getattr(completed, "stdout", "") or "").strip()

def _stderr(completed: object) -> str:
    return str(getattr(completed, "stderr", "") or "").strip()

def _returncode(completed: object) -> int:
    return int(getattr(completed, "returncode", 1))

def _json_stdout(completed: object, context: str) -> object:
    if _returncode(completed) != 0:
        raise WindowsVerificationError(
            f"{context} failed: {(_stderr(completed) or _stdout(completed) or 'no output')[-2000:]}"
        )
    try:
        return json.loads(_stdout(completed) or "null")
    except json.JSONDecodeError as exc:
        raise WindowsVerificationError(f"{context} returned invalid JSON") from exc
