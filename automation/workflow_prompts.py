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
from automation import local_verification
from automation.semantic_contract import SemanticVerifierError
from automation.semantic_invocation import prepare_semantic_repair_prompt
from automation.semantic_prompts import extract_acceptance_criteria
from automation.semantic_schema import parse_semantic_output
from automation.semantic_text import render_template
from automation.workflow_commands import (
    _decoded_text,
    gh,
)
from automation.workflow_contract import (
    WorkflowStageError,
)
from automation.workflow_storage import (
    read_json,
    read_text,
    write_state,
    write_text,
)

def resolve_profiles(
    labels: list[str],
    profiles_path: Path,
    *,
    explicit_profiles: str,
    explicit_local_check: str,
    explicit_stack_context: str,
    autodev_root: Path,
    which: Callable[[str], str | None] = shutil.which,
    platform: str | None = None,
    cwd: Path | None = None,
) -> tuple[str, str, str]:
    config = read_json(profiles_path)
    if not isinstance(config, dict):
        config = {}
    if not config and not explicit_local_check.strip():
        raise WorkflowStageError(
            f"verification profile configuration is missing or invalid: {profiles_path}; set LOCAL_CHECK explicitly"
        )
    definitions = config.get("profiles", {})
    definitions = definitions if isinstance(definitions, dict) else {}
    selected = [value for value in re.split(r"[,;\s]+", explicit_profiles.casefold()) if value]
    if not selected:
        for key, value in definitions.items():
            if not isinstance(value, dict):
                continue
            profile_labels = [str(item) for item in value.get("labels", [])]
            if any(label in labels for label in profile_labels):
                selected.append(str(key))
    if not selected:
        selected = [str(config.get("defaultProfile", "auto") or "auto")]
    selected = list(dict.fromkeys(selected))
    if "auto" in selected and len(selected) > 1:
        selected = [item for item in selected if item != "auto"]

    verify_profiles: list[str] = []
    contexts: list[str] = []
    for profile_name in selected:
        value = definitions.get(profile_name, {}) if profile_name != "auto" else {}
        value = value if isinstance(value, dict) else {}
        verify_profiles.append(str(value.get("verifyProfile", profile_name)))
        context = str(value.get("stackContext", "")).strip()
        if context:
            contexts.append(context)
    profiles_csv = ",".join(dict.fromkeys(verify_profiles))
    explicit = explicit_local_check.strip()
    if explicit:
        local_check = explicit
    else:
        template = local_verification.resolve_template(config, platform=platform)
        if "{~{CodexToolsDir}~}" in template:
            local_check = local_verification.render_profile_command(
                config,
                profiles_csv=profiles_csv,
                autodev_root=autodev_root,
                platform=platform,
            )
        else:
            # Do not consult Path.home() for profiles that do not use the
            # CodexToolsDir placeholder. The shipped platform-neutral verifier
            # intentionally has no home-directory dependency.
            local_check = (
                template.replace("{~{ProfilesCsv}~}", profiles_csv)
                .replace("{~{AutomationRoot}~}", str(autodev_root))
                .strip()
            )
    local_verification.preflight_local_check(
        local_check,
        explicit=bool(explicit),
        profiles_path=profiles_path,
        autodev_root=autodev_root,
        cwd=cwd,
        platform=platform,
        which=which,
    )
    stack_context = explicit_stack_context.strip() or "\n".join(contexts)
    if not stack_context:
        stack_context = (
            "No specific area profile was selected. Use repository AGENTS.md, README, project files, "
            "solution/package files, and CI configuration as the source of truth. Prefer the smallest safe scope."
        )
    return profiles_csv, local_check, stack_context

def render_implementer_prompt(repo: Path, current: Path, state: dict[str, object], autodev_root: Path) -> None:
    plan = read_text(current / "plan.md")
    if not plan.strip():
        raise WorkflowStageError("cannot render implementer prompt because plan.md is missing")
    template = read_text(autodev_root / "promptTemplates" / "implementer.md")
    prompt = render_template(
        template,
        {
            "IssueText": read_text(current / "issue.md") or str(state.get("IssueText", "")),
            "Plan": plan,
            "LocalCheck": str(state.get("LocalCheck", "")),
            "StackContext": str(state.get("StackContext", "")),
        },
    )
    write_text(current / "implementer.md", prompt)
    state["Status"] = "ImplementerPromptRendered"
    write_state(current, state)

def render_ci_repair(current: Path, state: dict[str, object], autodev_root: Path) -> None:
    prompt = render_template(
        read_text(autodev_root / "promptTemplates" / "ci-repair.md"),
        {
            "IssueText": read_text(current / "issue.md") or str(state.get("IssueText", "")),
            "Plan": read_text(current / "plan.md"),
            "CiSummary": read_text(current / "ci-summary.json"),
            "LocalCheck": str(state.get("LocalCheck", "")),
            "StackContext": str(state.get("StackContext", "")),
        },
    )
    write_text(current / "ci-repair.md", prompt)

def commit_message(current: Path, state: dict[str, object]) -> str:
    lines = read_text(current / "commit-message.txt").splitlines()
    if lines and lines[0].strip():
        return lines[0].strip()[:200]
    number = int(state.get("IssueNumber", 0) or 0)
    title = str(state.get("IssueTitle", "")).strip()
    return f"Implement issue-{number}: {title}" if title else f"Implement issue-{number} via AutoDev"