from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from automation.semantic_contract import SemanticVerifierError
from automation.semantic_invocation import prepare_semantic_repair_prompt
from automation.semantic_prompts import extract_acceptance_criteria
from automation.semantic_schema import parse_semantic_output
from automation.semantic_text import render_template
from automation.workflow_contract import (
    FAILURE_DETERMINISTIC,
    FAILURE_TRANSIENT,
    WorkflowStageError,
    concise,
)

def gh(
    repo: Path,
    arguments: list[str],
    *,
    input_text: str | None = None,
    runner: Callable[..., object] = subprocess.run,
    check: bool = True,
):
    completed = _run_captured(
        runner,
        ["gh", *arguments],
        cwd=repo,
        input_text=input_text,
        env=_gh_environment(),
    )
    if check and int(getattr(completed, "returncode", 1)) != 0:
        raise WorkflowStageError(
            _command_reason(completed),
            classification=_command_failure_classification(completed),
        )
    return completed

def git(
    repo: Path,
    arguments: list[str],
    *,
    runner: Callable[..., object] = subprocess.run,
    check: bool = True,
):
    completed = _run_captured(
        runner,
        ["git", *arguments],
        cwd=repo,
    )
    if check and int(getattr(completed, "returncode", 1)) != 0:
        raise WorkflowStageError(_command_reason(completed))
    return completed

def gh_json(
    repo: Path,
    arguments: list[str],
    *,
    input_text: str | None = None,
    runner: Callable[..., object] = subprocess.run,
) -> dict[str, object]:
    completed = gh(repo, arguments, input_text=input_text, runner=runner)
    text = _decoded_text(getattr(completed, "stdout", "")).strip()
    if "\ufffd" in text:
        raise WorkflowStageError(
            f"gh returned invalid JSON for {' '.join(arguments)}: output contained invalid UTF-8 bytes: {concise(text, 700)}"
        )
    try:
        value = json.loads(text or "{}")
    except json.JSONDecodeError as exc:
        raise WorkflowStageError(
            f"gh returned invalid JSON for {' '.join(arguments)}: {concise(text, 700)}"
        ) from exc
    if not isinstance(value, dict):
        raise WorkflowStageError(
            f"gh returned an unexpected JSON value for {' '.join(arguments)}: {concise(text, 700)}"
        )
    return value

def _run_captured(
    runner: Callable[..., object],
    command: object,
    *,
    cwd: Path,
    shell: bool = False,
    input_text: str | None = None,
    env: dict[str, str] | None = None,
):
    kwargs: dict[str, object] = {
        "cwd": cwd,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "capture_output": True,
        "check": False,
    }
    if shell:
        kwargs["shell"] = True
    if input_text is not None:
        kwargs["input"] = input_text
    if env is not None:
        kwargs["env"] = env
    return runner(command, **kwargs)

def _decoded_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)

def _gh_environment() -> dict[str, str]:
    env = dict(os.environ)
    env["GH_PROMPT_DISABLED"] = "1"
    return env

def _command_reason(completed: object) -> str:
    stderr = _decoded_text(getattr(completed, "stderr", ""))
    stdout = _decoded_text(getattr(completed, "stdout", ""))
    code = int(getattr(completed, "returncode", 1))
    evidence = (stderr or stdout or "no command output").strip()
    return concise(f"command exited with {code}: {evidence}")

def _command_failure_classification(completed: object) -> str:
    text = (
        _decoded_text(getattr(completed, "stderr", ""))
        + " "
        + _decoded_text(getattr(completed, "stdout", ""))
    ).casefold()
    transient_markers = (
        "timed out",
        "timeout",
        "temporarily unavailable",
        "connection reset",
        "connection refused",
        "network",
        "rate limit",
        "http 429",
        "http 500",
        "http 502",
        "http 503",
        "http 504",
    )
    return FAILURE_TRANSIENT if any(marker in text for marker in transient_markers) else FAILURE_DETERMINISTIC

def _porcelain_paths(value: str) -> list[str]:
    paths: list[str] = []
    for line in value.splitlines():
        if len(line) < 4:
            continue
        path = line[3:].strip().strip('"')
        if " -> " in path:
            path = path.rsplit(" -> ", 1)[-1]
        if path:
            paths.append(path.replace("\\", "/"))
    return paths
