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
    DEFAULT_BLOCKED_LABEL,
    DEFAULT_CODER_MODEL,
    DEFAULT_DONE_LABEL,
    DEFAULT_FAILED_LABEL,
    DEFAULT_READER_MODEL,
    DEFAULT_READY_LABEL,
    DEFAULT_RUNNING_LABEL,
    RunnerError,
)

def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run an AutoDev issue-to-PR flow with provider-agnostic reader and coder models."
    )
    parser.add_argument("--repo", required=True, help="Local repository path to operate on.")
    parser.add_argument("--github-repo", required=True, help="GitHub repository in owner/name form.")
    issue_group = parser.add_mutually_exclusive_group(required=True)
    issue_group.add_argument("--issue", type=positive_int, help="GitHub issue number.")
    issue_group.add_argument("--next", action="store_true", help="Select the next eligible issue.")
    parser.add_argument("--mode", choices=("plan-only", "implement", "pr"), default="plan-only")
    parser.add_argument("--out", required=True, help="Output directory for concise run artifacts.")
    parser.add_argument("--debug-artifacts", action="store_true", help="Keep benchmark-style raw area-reader artifacts.")
    parser.add_argument("--allow-dirty", action="store_true", help="Allow running when the repo has uncommitted changes.")
    parser.add_argument("--provider-config", help="Optional JSON provider configuration file.")

    add_provider_args(parser, "reader", DEFAULT_READER_MODEL)
    add_provider_args(parser, "coder", DEFAULT_CODER_MODEL)

    parser.add_argument("--max-fix-attempts", type=non_negative_int, default=2)
    parser.add_argument("--skip-implementation", action="store_true")
    parser.add_argument("--dry-run-implementation", action="store_true")
    parser.add_argument("--baseline-verify", action="store_true")

    parser.add_argument("--ready-label", default=DEFAULT_READY_LABEL)
    parser.add_argument("--running-label", default=DEFAULT_RUNNING_LABEL)
    parser.add_argument("--failed-label", default=DEFAULT_FAILED_LABEL)
    parser.add_argument("--done-label", default=DEFAULT_DONE_LABEL)
    parser.add_argument("--blocked-label", default=DEFAULT_BLOCKED_LABEL)
    parser.add_argument("--limit", type=positive_int, default=25)
    parser.add_argument("--selection", choices=("oldest", "newest"), default="oldest")
    parser.add_argument("--manage-labels", action="store_true")
    return parser.parse_args(argv)

def add_provider_args(parser: argparse.ArgumentParser, role: str, default_model: str) -> None:
    parser.add_argument(f"--{role}-provider", choices=("command", "chat-completions", "openai-compatible", "mock"))
    parser.add_argument(f"--{role}-command")
    parser.add_argument(f"--{role}-base-url")
    parser.add_argument(f"--{role}-model", default=None, help=f"{role.title()} model name. Default: {default_model}.")
    parser.add_argument(f"--{role}-api-key-env")
    parser.add_argument(f"--{role}-timeout-seconds", type=positive_int)
    legacy = "--reader" if role == "reader" else "--coder"
    parser.add_argument(legacy, dest=f"{role}_model", help=argparse.SUPPRESS)

def positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("value must be an integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be greater than zero")
    return parsed

def non_negative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("value must be zero or greater")
    return parsed

def expand_path(value: str) -> Path:
    return Path(os.path.expanduser(value)).resolve()

def validate_inputs(args: argparse.Namespace, repo: Path) -> None:
    if not repo.is_dir():
        raise RunnerError(f"--repo is not a directory: {repo}", 2)
    if "/" not in args.github_repo or args.github_repo.count("/") != 1:
        raise RunnerError("--github-repo must use owner/name format", 2)
    if not Path(area_reader_runner.__file__).is_file():
        raise RunnerError(f"Missing area-reader v2 runner module: {area_reader_runner.__file__}", 2)
    if args.mode == "plan-only" and args.dry_run_implementation:
        raise RunnerError("--dry-run-implementation is only valid for implement or pr mode", 2)

def resolve_provider_configs(args: argparse.Namespace) -> tuple[ModelConfig, ModelConfig]:
    file_config = load_provider_config(args.provider_config)
    defaults = {
        "reader": default_ollama_command_config(DEFAULT_READER_MODEL),
        "coder": default_ollama_command_config(DEFAULT_CODER_MODEL),
    }
    reader = resolve_model_config(
        "reader",
        defaults=defaults["reader"],
        file_config=file_config,
        cli_values=provider_cli_values(args, "reader", file_config, defaults["reader"]),
    )
    coder = resolve_model_config(
        "coder",
        defaults=defaults["coder"],
        file_config=file_config,
        cli_values=provider_cli_values(args, "coder", file_config, defaults["coder"]),
    )
    return reader, coder

def default_ollama_command_config(model: str) -> dict[str, object]:
    return {
        "provider": "command",
        "model": model,
        "command": ollama_command_for_model(model),
        "timeout_seconds": 600,
    }

def provider_cli_values(
    args: argparse.Namespace,
    role: str,
    file_config: dict[str, object] | None = None,
    defaults: dict[str, object] | None = None,
) -> dict[str, object]:
    values: dict[str, object] = {
        "provider": getattr(args, f"{role}_provider"),
        "command": getattr(args, f"{role}_command"),
        "base_url": getattr(args, f"{role}_base_url"),
        "model": getattr(args, f"{role}_model"),
        "api_key_env": getattr(args, f"{role}_api_key_env"),
        "timeout_seconds": getattr(args, f"{role}_timeout_seconds"),
    }
    if file_config is not None and defaults is not None:
        add_default_ollama_command(role, values, file_config, defaults)
    return values

def add_default_ollama_command(
    role: str,
    cli_values: dict[str, object],
    file_config: dict[str, object],
    defaults: dict[str, object],
) -> None:
    role_config = file_config.get(role, {})
    if role_config and not isinstance(role_config, dict):
        return
    role_values = role_config if isinstance(role_config, dict) else {}
    provider = cli_values.get("provider") or role_values.get("provider") or defaults.get("provider")
    command = cli_values.get("command") or role_values.get("command")
    if provider != "command" or command:
        return
    model = cli_values.get("model") or role_values.get("model") or defaults.get("model")
    if model:
        cli_values["command"] = ollama_command_for_model(str(model))
