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
from area_reader_v2 import runner as area_reader_runner
from area_reader_v2.command_group_recommendations import documentation_only_command_groups, is_documentation_only_scope
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
    RunnerError,
)

def run_area_reader(
    repo: Path,
    issue_text: str,
    reader_config: ModelConfig,
    coder_config: ModelConfig,
    out_dir: Path,
    stream: TextIO,
) -> None:
    if out_dir.exists():
        shutil.rmtree(out_dir)
    argv = [
        "--repo",
        str(repo),
        "--reader-provider",
        reader_config.provider,
        "--reader-model",
        reader_config.model,
        "--reader-timeout-seconds",
        str(reader_config.timeout_seconds),
        "--coder-provider",
        coder_config.provider,
        "--coder-model",
        coder_config.model,
        "--coder-timeout-seconds",
        str(coder_config.timeout_seconds),
        "--issue",
        issue_text,
        "--out",
        str(out_dir),
    ]
    append_provider_command_args(argv, "reader", reader_config)
    append_provider_command_args(argv, "coder", coder_config)
    print("Running shared area-reader v2 planner", file=stream)
    exit_code = area_reader_runner.main(argv)
    if exit_code:
        raise RunnerError(f"area-reader v2 planner failed with exit code {exit_code}", exit_code)

def append_provider_command_args(command: list[str], role: str, config: ModelConfig) -> None:
    if config.command:
        command.extend([f"--{role}-command", config.command])
    if config.base_url:
        command.extend([f"--{role}-base-url", config.base_url])
    if config.api_key_env:
        command.extend([f"--{role}-api-key-env", config.api_key_env])
