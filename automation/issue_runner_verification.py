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
from automation.issue_runner_commands import (
    run_command,
)
from automation.issue_runner_contract import (
    VerificationResult,
)
from automation.issue_runner_storage import (
    read_json,
    trim_log,
    write_text,
)

def run_recommended_verification(out_dir: Path, repo: Path, attempt: int, stream: TextIO) -> VerificationResult:
    groups = read_json(out_dir / "verification-command-groups.json")
    recommendations = read_json(out_dir / "recommended-command-groups.json")
    recommended = recommendations.get("recommended_command_groups", []) if isinstance(recommendations, dict) else []
    selected = [
        group for group in groups
        if isinstance(group, dict) and group.get("name") in recommended and not group.get("manual")
    ] if isinstance(groups, list) else []
    stdout_parts: list[str] = []
    stderr_parts: list[str] = []
    for group in selected:
        group_name = str(group.get("name") or "unknown")
        stdout_parts.append(f"== {group_name} ==")
        commands = group.get("commands") or []
        if not commands:
            stdout_parts.append(str(group.get("reason") or "No commands in this group."))
            continue
        for item in commands:
            if not isinstance(item, dict):
                continue
            argv = [str(part) for part in item.get("argv", [])]
            if not argv:
                continue
            cwd = repo / str(item.get("cwd") or ".")
            result = run_command(argv, cwd=cwd, stream=stream, check=False)
            stdout_parts.append(result.stdout)
            if result.stderr:
                stderr_parts.append(result.stderr)
            if result.returncode != 0 and not item.get("optional"):
                verification = VerificationResult(
                    attempt,
                    result.returncode,
                    group_name,
                    "\n".join(stdout_parts),
                    "\n".join(stderr_parts),
                    out_dir / "verification" / f"attempt-{attempt}.md",
                )
                write_verification_attempt(verification)
                return verification
    verification = VerificationResult(
        attempt,
        0,
        ",".join(str(group.get("name")) for group in selected) or "none",
        "\n".join(stdout_parts),
        "\n".join(stderr_parts),
        out_dir / "verification" / f"attempt-{attempt}.md",
    )
    write_verification_attempt(verification)
    return verification

def write_verification_attempt(result: VerificationResult) -> None:
    write_text(result.summary_path, render_verification_summary(result))

def write_verification_result(out_dir: Path, result: VerificationResult) -> None:
    write_text(out_dir / "verification-result-summary.md", render_verification_summary(result))

def render_verification_summary(result: VerificationResult) -> str:
    return "\n".join(
        [
            "# Verification Result Summary",
            "",
            f"Attempt: {result.attempt}",
            f"Command group: {result.command_group}",
            f"Exit code: {result.returncode}",
            "",
            "## Output",
            "",
            trim_log(result.stdout),
            "",
            "## Error Output",
            "",
            trim_log(result.stderr),
            "",
        ]
    )
