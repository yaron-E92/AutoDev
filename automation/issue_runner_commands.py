from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, TextIO
from area_reader import workflow as area_reader_runner
from area_reader.recommendations import documentation_only_command_groups, is_documentation_only_scope
from automation.model_output_sanitizer import sanitize_model_output
from automation.model_providers import (
    ModelConfig,
    ModelProvider,
    ProviderError,
    create_provider,
    load_provider_config,
    ollama_command_for_model,
    resolve_model_config,
)
from automation.issue_runner_contract import (
    CommandResult,
    RunnerError,
)

def require_tools(tools: list[str]) -> None:
    missing = [tool for tool in tools if shutil.which(tool) is None]
    if missing:
        raise RunnerError("Missing required executable(s): " + ", ".join(missing), 127)

def print_command(argv: list[str], cwd: Path, stream: TextIO) -> None:
    print(f"+ ({cwd}) {subprocess.list2cmdline(argv)}", file=stream)

def run_command(
    argv: list[str],
    *,
    cwd: Path,
    stream: TextIO,
    check: bool = True,
    timeout: int | None = None,
    input_text: str | None = None,
) -> CommandResult:
    print_command(argv, cwd, stream)
    completed = subprocess.run(
        argv,
        cwd=cwd,
        input=input_text,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    result = CommandResult(argv, cwd, completed.returncode, completed.stdout, completed.stderr)
    if check and result.returncode != 0:
        raise RunnerError(format_command_failure(result))
    return result

def format_command_failure(result: CommandResult) -> str:
    return "\n".join(
        part
        for part in (
            f"Command failed with exit code {result.returncode}: {subprocess.list2cmdline(result.argv)}",
            result.stdout.strip(),
            result.stderr.strip(),
        )
        if part
    )
