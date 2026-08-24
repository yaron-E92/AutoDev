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
    FALLBACK_SYNTHESIZED_HANDOFF,
    NO_CHANGES_REQUIRED,
    PATCH_END,
    PATCH_START,
    PROMPT_TEMPLATE_DIR,
    VerificationResult,
)
from automation.issue_runner_storage import (
    read_json,
    read_optional_text,
    trim_log,
    write_text,
)

def collect_area_reader_relevant_files(out_dir: Path, workspace_snapshot: object) -> list[str]:
    workspace_paths = set(workspace_snapshot.keys()) if isinstance(workspace_snapshot, dict) else set()
    files: set[str] = set()
    summary = read_json(out_dir / "area-reader-summary.json")
    if not summary:
        summary = read_json(out_dir / "summary.json")
    if isinstance(summary, dict):
        area_metadata = summary.get("area_metadata")
        if isinstance(area_metadata, dict):
            for metadata in area_metadata.values():
                if not isinstance(metadata, dict):
                    continue
                for path in metadata.get("included_files", []):
                    add_workspace_path(files, str(path), workspace_paths)
        collect_workspace_paths(summary.get("detected_facts"), files, workspace_paths)
    collect_workspace_paths(read_json(out_dir / "detected-facts.json"), files, workspace_paths)
    return sorted(files)

def collect_workspace_paths(value: object, files: set[str], workspace_paths: set[str]) -> None:
    if isinstance(value, dict):
        for item in value.values():
            collect_workspace_paths(item, files, workspace_paths)
    elif isinstance(value, list):
        for item in value:
            collect_workspace_paths(item, files, workspace_paths)
    elif isinstance(value, str):
        add_workspace_path(files, value, workspace_paths)

def add_workspace_path(files: set[str], path: str, workspace_paths: set[str]) -> None:
    normalized = path.replace("\\", "/").strip()
    if not normalized or normalized.startswith("/"):
        return
    if workspace_paths and normalized not in workspace_paths:
        return
    if any(marker in normalized for marker in ("\n", "\r", "*")):
        return
    if "/" in normalized or "." in Path(normalized).name:
        files.add(normalized)

def workspace_snapshot_summary(workspace_snapshot: object, limit: int = 200) -> str:
    if not isinstance(workspace_snapshot, dict):
        return "{}"
    paths = sorted(str(path) for path in workspace_snapshot.keys())
    return json.dumps(
        {
            "path_count": len(paths),
            "paths": paths[:limit],
            "truncated": len(paths) > limit,
        },
        indent=2,
        sort_keys=True,
    )

def usable_synthesized_handoff(value: str) -> str:
    cleaned = sanitize_model_output(value)
    if not cleaned:
        return ""
    lowered = cleaned.casefold()
    if lowered.startswith("thinking") or lowered.startswith("scratchpad") or lowered.startswith("reasoning"):
        return ""
    if len(cleaned) < 40:
        return ""
    return cleaned

def synthesized_handoff_or_fallback(value: str) -> str:
    return usable_synthesized_handoff(value) or FALLBACK_SYNTHESIZED_HANDOFF

def planner_handoff_section(value: str) -> str:
    return synthesized_handoff_or_fallback(value)

def build_area_reader_planner_prompt(
    *,
    issue_text: str,
    local_check: str,
    labels: list[str],
    profile_context_hints: str,
    routed_areas: object,
    synthesized_handoff: str,
    coder_plan: str,
    relevant_files: list[str],
    recommended_command_groups: object,
    workspace_snapshot: object,
) -> str:
    return f"""Use the issue-to-pr-automation skill.

You are the Planner for this repository.

Operating mode: PLAN ONLY - NO CODE.

Area-reader routed areas:
{json.dumps(routed_areas, indent=2, sort_keys=True)}

Area-reader synthesized handoff:
{planner_handoff_section(synthesized_handoff)}

Area-reader coder / implementation plan:
{sanitize_model_output(coder_plan)}

Detected relevant files from area-reader facts:
{json.dumps(relevant_files, indent=2, sort_keys=True)}

Recommended command groups:
{json.dumps(recommended_command_groups, indent=2, sort_keys=True)}

Workspace snapshot grounding:
{workspace_snapshot_summary(workspace_snapshot)}

Routing hints only:
- GitHub labels: {', '.join(labels) if labels else '(none)'}
- Profile context hints: {profile_context_hints.strip() or '(none)'}

Automation context:
- The configured local verification command is: {local_check}
- Build/run/tests are handled by the automation script unless explicitly stated otherwise.
- Do not modify files.

Goal:
Plan the implementation of the issue below as a fast, localized change with minimal risk.

Constraints:
- Treat labels and profile text as routing hints only. Use area-reader synthesis and repository facts as the final planning scope.
- Ground every file or path in the workspace snapshot and area-reader facts. Do not invent paths.
- Let area-reader synthesis narrow touched areas/files based on the issue text and repository facts.
- Do NOT over-decompose.
- Use at most 4 implementation steps.
- Touch as few files as possible, preferably 1-3 files.
- Prefer editing existing code over creating new abstractions.
- Avoid task stubs, TODO-only work, and speculative architecture.
- Do not change domain logic, persistence, models, migrations, public APIs, schemas, scoring, task state logic, or unrelated behavior unless the issue explicitly requires it.
- If something is unclear, make a reasonable assumption and call it out briefly.
- If the issue is too broad for a localized change, say so clearly and propose the smallest safe slice.

Output format:
1) Where to look
   - Exact search terms or likely files/components to inspect
   - Max 6 bullets
2) Files / areas likely to touch
   - Best guess list, using only existing paths supported by the workspace snapshot or area-reader facts
3) Assumptions
   - Max 5 bullets
4) Plan
   - Max 4 bullets
   - Fastest sensible implementation path
5) Risks / gotchas
   - Max 5 bullets
6) Recommended implementation approach
   - Option A: fastest / lowest-risk
   - Option B: slightly cleaner, only if Option A is blocked or too messy

Rules:
- No code.
- No pseudo-code.
- No refactoring wishlist.
- Keep the plan implementer-ready.
- Output only the final plan. Do not include thinking, hidden reasoning, scratchpad text, or model preamble.

Issue:
{issue_text}
"""

def build_planner_prompt_from_area_reader(current_dir: Path, issue_text: str, local_check: str, labels: list[str], profile_context_hints: str) -> str:
    workspace_snapshot = read_json(current_dir / "workspace-snapshot.json")
    return build_area_reader_planner_prompt(
        issue_text=issue_text,
        local_check=local_check,
        labels=labels,
        profile_context_hints=profile_context_hints,
        routed_areas=read_json(current_dir / "routed-areas.json"),
        synthesized_handoff=read_optional_text(current_dir / "synthesized-handoff.md"),
        coder_plan=read_optional_text(current_dir / "coder-plan.md"),
        relevant_files=collect_area_reader_relevant_files(current_dir, workspace_snapshot),
        recommended_command_groups=read_json(current_dir / "recommended-command-groups.json"),
        workspace_snapshot=workspace_snapshot,
    )

def build_implementation_prompt(
    *,
    issue_text: str,
    synthesized_handoff: str,
    coder_plan: str,
    recommended_command_groups: str,
    constraints: str,
    branch_name: str,
) -> str:
    return f"""You are the coder model for AutoDev.

The AutoDev runner will apply your patch and run deterministic verification. You must not run shell commands.

Issue:
{issue_text}

Synthesized handoff:
{synthesized_handoff}

Coder plan:
{coder_plan}

Recommended command groups JSON:
{recommended_command_groups}

Repository constraints:
{constraints}

Current branch:
{branch_name}

Rules:
- Make minimal, issue-scoped changes.
- Avoid unrelated refactors.
- Preserve existing style and file organization.
- Output only one of the required response shapes.
- Use a unified git diff inside {PATCH_START} and {PATCH_END}.
- Output {NO_CHANGES_REQUIRED} only if the issue is already fully satisfied.
- Do not include prose outside the required markers.

Patch response contract:
{PATCH_START}
<unified git diff>
{PATCH_END}

No-change response contract:
{NO_CHANGES_REQUIRED}
<short explanation>
"""

def build_fix_prompt(
    *,
    issue_text: str,
    synthesized_handoff: str,
    coder_plan: str,
    previous_response: str,
    current_diff: str,
    verification: VerificationResult,
) -> str:
    return f"""You are the fixer model for AutoDev.

Produce a minimal corrective unified diff only. The AutoDev runner will apply it and rerun verification.

Original issue:
{issue_text}

Synthesized handoff:
{synthesized_handoff}

Coder plan:
{coder_plan}

Previous model response summary:
{trim_log(previous_response, 4000)}

Current git diff:
{current_diff}

Verification exit code:
{verification.returncode}

Failed command group:
{verification.command_group}

Verification stdout:
{trim_log(verification.stdout)}

Verification stderr:
{trim_log(verification.stderr)}

Output only:
{PATCH_START}
<minimal corrective unified git diff>
{PATCH_END}
"""

def write_implementation_prompt_file(out_dir: Path, issue_text: str, branch_name: str) -> None:
    write_text(
        out_dir / "implementation-prompt.md",
        build_implementation_prompt(
            issue_text=issue_text,
            synthesized_handoff=read_optional_text(out_dir / "synthesized-handoff.md"),
            coder_plan=read_optional_text(out_dir / "coder-plan.md"),
            recommended_command_groups=read_optional_text(out_dir / "recommended-command-groups.json"),
            constraints=read_optional_text(PROMPT_TEMPLATE_DIR / "implementer.md"),
            branch_name=branch_name,
        ),
    )

def current_diff(repo: Path, stream: TextIO) -> str:
    result = run_command(["git", "diff"], cwd=repo, stream=stream, check=False)
    return result.stdout
