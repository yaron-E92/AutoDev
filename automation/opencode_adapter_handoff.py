from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from area_reader import context as area_reader_context
from area_reader import prompts as area_reader_prompts
from area_reader import repository as area_reader_repository
from area_reader import routing as area_reader_routing
from area_reader import verification as area_reader_verification
from automation.model_output_sanitizer import sanitize_model_output
from automation.planner_output import (
    REQUIRED_PLAN_HEADINGS,
    PlannerOutputError,
    handle_planner_output,
)

from automation.opencode_adapter_contract import (
    MAX_HANDOFF_CHARS,
    MAX_READER_BUNDLE_CHARS,
    OpenCodeAdapterError,
)
from automation.opencode_adapter_storage import (
    _read_json,
    _read_text,
    _write_json,
    _write_text,
)

def _next_semantic_attempt(current: Path) -> int:
    verification = current / "verification"
    attempts: list[int] = []
    for path in verification.glob("semantic-attempt-*.json") if verification.is_dir() else ():
        match = re.fullmatch(r"semantic-attempt-(\d+)\.json", path.name)
        if match:
            attempts.append(int(match.group(1)))
    return max(attempts, default=-1) + 1

def _prepare_reader(repo: Path, current: Path, issue_text: str) -> str:
    files, skipped_large, skipped_unreadable = area_reader_repository.collect_repo_files(repo)
    repo_map = area_reader_repository.build_repo_map(repo, files, skipped_large, skipped_unreadable)
    areas, routing = area_reader_routing.route_areas(issue_text, "auto")
    facts = area_reader_repository.detect_repo_facts(repo, files, areas, routing)
    groups = area_reader_verification.build_verification_command_groups(facts, areas)
    recommendations = area_reader_verification.recommended_command_groups(
        groups,
        issue_text=issue_text,
        changed_paths=(),
    )
    area_reader_verification.apply_recommended_command_groups(groups, recommendations)
    _write_json(current / "routed-areas.json", {"areas": areas, **routing})
    _write_json(current / "detected-facts.json", facts)
    _write_json(current / "verification-command-groups.json", groups)
    _write_json(current / "recommended-command-groups.json", recommendations)

    bundle, included = _bounded_reader_bundle(repo, files, areas, repo_map)
    metadata = {
        "routed_areas": areas,
        "included_files": included,
        "bundle_chars": len(bundle),
        "max_chars": MAX_READER_BUNDLE_CHARS,
        "truncated": len(bundle) >= MAX_READER_BUNDLE_CHARS,
    }
    return area_reader_prompts.build_area_reader_prompt(
        issue_text,
        ",".join(areas) or "repository",
        bundle,
        metadata,
    )

def _bounded_reader_bundle(
    repo: Path,
    files: list[dict[str, object]],
    areas: list[str],
    repo_map: str,
) -> tuple[str, list[str]]:
    header = (
        "Routed areas: "
        + ", ".join(areas)
        + "\n\nRepository map:\n"
        + repo_map[:8_000]
        + "\nRelevant file excerpts:\n"
    )
    parts = [header]
    included: list[str] = []
    remaining = MAX_READER_BUNDLE_CHARS - len(header)
    for item in files:
        item_areas = item.get("areas", [])
        if not item.get("priority") and not any(area in item_areas for area in areas):
            continue
        relative = str(item.get("path", ""))
        if not relative:
            continue
        try:
            content = area_reader_context.read_file_for_bundle(repo, relative)
        except OSError:
            continue
        block = f"\n===== FILE: {relative} =====\n{content.rstrip()}\n"
        if remaining <= 0:
            break
        if len(block) > remaining:
            block = block[:remaining]
        parts.append(block)
        included.append(relative)
        remaining -= len(block)
    return "".join(parts)[:MAX_READER_BUNDLE_CHARS], included

def _prepare_synthesizer(current: Path, issue_text: str) -> str:
    brief = _read_text(current / "reader-brief.md") or _read_text(current / "synthesized-handoff.md")
    if not brief.strip():
        raise OpenCodeAdapterError("reader output is missing; run /autodev-read first")
    routed = _read_json(current / "routed-areas.json")
    areas = [str(value) for value in routed.get("areas", [])] if isinstance(routed, dict) else []
    facts = _read_json(current / "detected-facts.json")
    groups = _read_json(current / "verification-command-groups.json")
    return area_reader_prompts.build_synthesis_prompt(
        issue_text,
        areas,
        [
            {
                "area": "opencode-reader",
                "brief": brief[:MAX_HANDOFF_CHARS],
                "metadata": {"source": "reader-brief.md"},
            }
        ],
        facts,
        groups,
    )

def _fixer_source(current: Path, arguments: str) -> Path:
    lowered = (arguments or "").casefold()
    preferred: list[Path] = []
    if "semantic" in lowered or "verifier" in lowered:
        preferred.append(current / "verification-repair.md")
    if "ci" in lowered:
        preferred.append(current / "ci-repair.md")
    if "local" in lowered or "deterministic" in lowered:
        preferred.append(current / "local-repair.md")
    preferred.extend(
        [
            current / "verification-repair.md",
            current / "local-repair.md",
            current / "ci-repair.md",
        ]
    )
    existing: list[Path] = []
    seen: set[Path] = set()
    for path in preferred:
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        existing.append(path)
    if not existing:
        raise OpenCodeAdapterError(
            "no repair artifact is available; use the existing AutoDev verification/local-check stage first"
        )
    if not any(token in lowered for token in ("semantic", "verifier", "ci", "local", "deterministic")):
        existing.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return existing[0]

def _write_plan_template(current: Path) -> None:
    _write_text(
        current / "plan.template.md",
        "\n\n".join(REQUIRED_PLAN_HEADINGS) + "\n",
    )

def _plan_text(current: Path) -> str:
    return _read_text(current / "plan.md") or _read_text(current / "coder-plan.md")

def _bounded_result(path: Path) -> str:
    value = sanitize_model_output(_read_text(path))
    if not value:
        raise OpenCodeAdapterError(f"role result is empty: {path}")
    if len(value) > MAX_HANDOFF_CHARS:
        raise OpenCodeAdapterError(
            f"role result exceeds the {MAX_HANDOFF_CHARS}-character AutoDev handoff limit: {path}"
        )
    return value

def _bounded_text(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    digest = hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()
    return value[:limit] + f"\n[truncated; sha256={digest}]\n"


def _collect_workspace_paths(value: object, files: set[str], workspace_paths: set[str]) -> None:
    if isinstance(value, dict):
        for item in value.values():
            _collect_workspace_paths(item, files, workspace_paths)
    elif isinstance(value, list):
        for item in value:
            _collect_workspace_paths(item, files, workspace_paths)
    elif isinstance(value, str):
        normalized = value.replace("\\", "/").strip()
        if (
            normalized
            and not normalized.startswith("/")
            and not any(marker in normalized for marker in ("\n", "\r", "*"))
            and (not workspace_paths or normalized in workspace_paths)
            and ("/" in normalized or "." in Path(normalized).name)
        ):
            files.add(normalized)


def _area_reader_relevant_files(current: Path, workspace_snapshot: object) -> list[str]:
    workspace_paths = set(workspace_snapshot) if isinstance(workspace_snapshot, dict) else set()
    files: set[str] = set()
    _collect_workspace_paths(_read_json(current / "detected-facts.json"), files, workspace_paths)
    return sorted(files)


def _workspace_snapshot_summary(workspace_snapshot: object, limit: int = 200) -> str:
    if not isinstance(workspace_snapshot, dict):
        return "{}"
    paths = sorted(str(path) for path in workspace_snapshot)
    return json.dumps(
        {"path_count": len(paths), "paths": paths[:limit], "truncated": len(paths) > limit},
        indent=2,
        sort_keys=True,
    )


def build_planner_prompt_from_area_reader(
    current: Path,
    issue_text: str,
    local_check: str,
    labels: list[str],
    profile_context_hints: str,
) -> str:
    workspace_snapshot = _read_json(current / "workspace-snapshot.json")
    routed_areas = _read_json(current / "routed-areas.json")
    synthesized_handoff = sanitize_model_output(_read_text(current / "synthesized-handoff.md"))
    coder_plan = sanitize_model_output(_read_text(current / "coder-plan.md"))
    recommendations = _read_json(current / "recommended-command-groups.json")
    relevant_files = _area_reader_relevant_files(current, workspace_snapshot)
    return f"""Use the issue-to-pr-automation skill.

You are the Planner for this repository.

Operating mode: PLAN ONLY - NO CODE.

Area-reader routed areas:
{json.dumps(routed_areas, indent=2, sort_keys=True)}

Area-reader synthesized handoff:
{synthesized_handoff or '(no synthesized handoff available)'}

Area-reader coder / implementation plan:
{coder_plan}

Detected relevant files from area-reader facts:
{json.dumps(relevant_files, indent=2, sort_keys=True)}

Recommended command groups:
{json.dumps(recommendations, indent=2, sort_keys=True)}

Workspace snapshot grounding:
{_workspace_snapshot_summary(workspace_snapshot)}

Routing hints only:
- GitHub labels: {', '.join(labels) if labels else '(none)'}
- Profile context hints: {profile_context_hints.strip() or '(none)'}

Automation context:
- The configured local verification command is: {local_check}
- Build/run/tests are handled by AutoDev unless explicitly stated otherwise.
- Do not modify files.

Goal:
Plan the implementation of the issue below as a fast, localized change with minimal risk.

Constraints:
- Treat labels and profile text as routing hints only. Use area-reader synthesis and repository facts as the final planning scope.
- Ground every file or path in the workspace snapshot and area-reader facts. Do not invent paths.
- Do NOT over-decompose.
- Use at most 4 implementation steps.
- Touch as few files as possible, preferably 1-3 files.
- Prefer editing existing code over creating new abstractions.
- Avoid task stubs, TODO-only work, and speculative architecture.
- If something is unclear, make a reasonable assumption and call it out briefly.

Output format:
1) Where to look
2) Files / areas likely to touch
3) Assumptions
4) Plan
5) Risks / gotchas
6) Recommended implementation approach

Rules:
- No code or pseudo-code.
- No refactoring wishlist.
- Keep the plan implementer-ready.
- Output only the final plan.

Issue:
{issue_text}
"""
