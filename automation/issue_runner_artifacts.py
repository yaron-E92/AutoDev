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
from automation.issue_runner_prompts import (
    collect_area_reader_relevant_files,
    synthesized_handoff_or_fallback,
)
from automation.issue_runner_storage import (
    read_json,
    read_optional_text,
    write_json,
    write_text,
)

def write_operational_outputs(issue_text: str, area_out: Path, out_dir: Path, keep_debug: bool) -> None:
    copies = {
        "routing.json": "routed-areas.json",
        "synthesis-brief.md": "synthesized-handoff.md",
        "coder-plan.md": "coder-plan.md",
        "recommended-command-groups.json": "recommended-command-groups.json",
        "verification-command-groups.json": "verification-command-groups.json",
        "detected-facts.json": "detected-facts.json",
        "summary.json": "area-reader-summary.json",
    }
    write_text(out_dir / "issue.md", issue_text)
    for source_name, target_name in copies.items():
        source = area_out / source_name
        if not source.is_file():
            continue
        target = out_dir / target_name
        if source.suffix in {".md", ".txt"}:
            content = sanitize_model_output(read_optional_text(source))
            if target_name == "synthesized-handoff.md":
                content = synthesized_handoff_or_fallback(content)
            write_text(target, sanitize_model_output(content, ensure_trailing_newline=True))
        else:
            shutil.copyfile(source, target)
    refine_recommendations_for_plan_scope(out_dir, issue_text)
    write_text(out_dir / "run-summary.md", build_run_summary(out_dir))
    if not keep_debug:
        shutil.rmtree(area_out, ignore_errors=True)

def refine_recommendations_for_plan_scope(out_dir: Path, issue_text: str) -> None:
    recommendations = read_json(out_dir / "recommended-command-groups.json")
    if not isinstance(recommendations, dict):
        return
    available = recommendations.get("available_command_groups")
    if not isinstance(available, list):
        available = recommendations.get("recommended_command_groups", [])
    available_set = {str(group) for group in available}
    coder_plan = read_optional_text(out_dir / "coder-plan.md")
    relevant_files = collect_area_reader_relevant_files(out_dir, {})
    scope_text = f"{issue_text}\n{coder_plan}"
    if not is_documentation_only_scope(scope_text.casefold(), relevant_files):
        return
    recommendations["recommended_command_groups"] = documentation_only_command_groups(relevant_files, available_set)
    write_text(out_dir / "recommended-command-groups.json", json.dumps(recommendations, indent=2, sort_keys=True) + "\n")

def build_run_summary(out_dir: Path) -> str:
    routing = read_json(out_dir / "routed-areas.json")
    recommendations = read_json(out_dir / "recommended-command-groups.json")
    areas = routing.get("areas", []) if isinstance(routing, dict) else []
    groups = recommendations.get("recommended_command_groups", []) if isinstance(recommendations, dict) else []
    return "\n".join(
        [
            "# AutoDev Real-Issue Run Summary",
            "",
            "Routed areas: " + (", ".join(str(area) for area in areas) if areas else "(none recorded)"),
            "Recommended verification groups: "
            + (", ".join(str(group) for group in groups) if groups else "(none recorded)"),
            "",
            "Primary outputs: issue.md, selected-issue.json, routed-areas.json, synthesized-handoff.md, "
            "coder-plan.md, recommended-command-groups.json, implementation-prompt.md, model-responses/, "
            "model-patches/, verification/, verification-result-summary.md, final-pr-summary.md, provider-metadata.json",
            "",
        ]
    )

def write_provider_metadata(out_dir: Path, reader_config: ModelConfig, coder_config: ModelConfig) -> None:
    write_json(
        out_dir / "provider-metadata.json",
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "reader": reader_config.safe_metadata(),
            "coder": coder_config.safe_metadata(),
        },
    )
