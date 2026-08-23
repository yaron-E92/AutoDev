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
from automation.issue_runner_commands import (
    format_command_failure,
    run_command,
)
from automation.issue_runner_contract import (
    NO_CHANGES_REQUIRED,
    PATCH_END,
    PATCH_START,
    PROMPT_TEMPLATE_DIR,
    RunnerError,
    VerificationResult,
)
from automation.issue_runner_prompts import (
    build_fix_prompt,
    build_implementation_prompt,
    current_diff,
)
from automation.issue_runner_storage import (
    read_optional_text,
    write_text,
)
from automation.issue_runner_verification import (
    run_recommended_verification,
    write_verification_result,
)

def run_implementation_loop(
    *,
    repo: Path,
    out_dir: Path,
    issue_text: str,
    branch_name: str,
    coder_provider: ModelProvider,
    coder_config: ModelConfig,
    max_fix_attempts: int,
    dry_run: bool,
    stream: TextIO,
) -> VerificationResult:
    prompt = build_implementation_prompt(
        issue_text=issue_text,
        synthesized_handoff=read_optional_text(out_dir / "synthesized-handoff.md"),
        coder_plan=read_optional_text(out_dir / "coder-plan.md"),
        recommended_command_groups=read_optional_text(out_dir / "recommended-command-groups.json"),
        constraints=read_optional_text(PROMPT_TEMPLATE_DIR / "implementer.md"),
        branch_name=branch_name,
    )
    write_text(out_dir / "implementation-prompt.md", prompt)
    response = call_coder(coder_provider, coder_config, prompt, out_dir, 0)
    patch = process_model_response(response, out_dir, 0)
    if patch is None:
        verification = VerificationResult(0, 0, "no-change", "NO_CHANGES_REQUIRED", "", out_dir / "verification" / "attempt-0.md")
        write_verification_result(out_dir, verification)
        return verification
    if dry_run:
        return VerificationResult(0, 0, "dry-run", "Dry-run implementation did not apply patch.", "", out_dir / "verification" / "attempt-0.md")
    apply_patch_file(repo, patch, stream)

    verification = run_recommended_verification(out_dir, repo, 0, stream)
    write_verification_result(out_dir, verification)
    attempt = 1
    while not verification.passed and attempt <= max_fix_attempts:
        fix_prompt = build_fix_prompt(
            issue_text=issue_text,
            synthesized_handoff=read_optional_text(out_dir / "synthesized-handoff.md"),
            coder_plan=read_optional_text(out_dir / "coder-plan.md"),
            previous_response=read_optional_text(out_dir / "model-responses" / f"attempt-{attempt - 1}.txt"),
            current_diff=current_diff(repo, stream),
            verification=verification,
        )
        write_text(out_dir / "fix-prompt.md", fix_prompt)
        response = call_coder(coder_provider, coder_config, fix_prompt, out_dir, attempt)
        patch = process_model_response(response, out_dir, attempt)
        if patch is None:
            break
        apply_patch_file(repo, patch, stream)
        verification = run_recommended_verification(out_dir, repo, attempt, stream)
        write_verification_result(out_dir, verification)
        attempt += 1
    return verification

def call_coder(provider: ModelProvider, config: ModelConfig, prompt: str, out_dir: Path, attempt: int) -> str:
    response = provider.generate(prompt, model=config.model, timeout_seconds=config.timeout_seconds)
    response_path = out_dir / "model-responses" / f"attempt-{attempt}.txt"
    write_text(response_path, response)
    return response

def process_model_response(response: str, out_dir: Path, attempt: int) -> Path | None:
    no_change = parse_no_changes_required(response)
    if no_change is not None:
        write_text(out_dir / "model-patches" / f"attempt-{attempt}.txt", NO_CHANGES_REQUIRED + "\n" + no_change.strip() + "\n")
        return None
    patch_text = extract_unified_diff(response)
    if not patch_text:
        raise RunnerError("model response did not contain a valid patch or NO_CHANGES_REQUIRED")
    patch_path = out_dir / "model-patches" / f"attempt-{attempt}.patch"
    write_text(patch_path, patch_text)
    return patch_path

def extract_unified_diff(response: str) -> str:
    start = response.find(PATCH_START)
    end = response.find(PATCH_END)
    if start < 0 or end < 0 or end <= start:
        return ""
    patch = response[start + len(PATCH_START):end].strip()
    if not patch.startswith("diff --git ") and not patch.startswith("--- "):
        return ""
    return patch + "\n"

def parse_no_changes_required(response: str) -> str | None:
    stripped = response.strip()
    if stripped == NO_CHANGES_REQUIRED:
        return ""
    if stripped.startswith(NO_CHANGES_REQUIRED + "\n"):
        return stripped[len(NO_CHANGES_REQUIRED):].strip()
    return None

def apply_patch_file(repo: Path, patch_path: Path, stream: TextIO) -> None:
    result = run_command(["git", "apply", "--index", str(patch_path)], cwd=repo, stream=stream, check=False)
    if result.returncode == 0:
        return
    fallback = run_command(["git", "apply", str(patch_path)], cwd=repo, stream=stream, check=False)
    if fallback.returncode != 0:
        raise RunnerError("patch application failed\n" + format_command_failure(fallback))
