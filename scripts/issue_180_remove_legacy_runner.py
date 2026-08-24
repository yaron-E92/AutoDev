from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AUTOMATION = ROOT / "automation"
TESTS = ROOT / "tests"

ATTRIBUTE_OWNER = {
    # contract
    **{name: "opencode_adapter_contract" for name in (
        "AGENT_FILES", "AUTODEV_AGENT_BY_ROLE", "AUTODEV_ROOT", "COMMAND_FILES",
        "COORDINATOR_STAGES", "CURRENT_DIR", "DEFAULT_MAX_REPAIR_ATTEMPTS",
        "DEFAULT_MAX_SEMANTIC_REPAIR_ATTEMPTS", "MAX_HANDOFF_CHARS",
        "MAX_READER_BUNDLE_CHARS", "OPENCODE_PROTOCOL_VERSION", "OPENCODE_ROLE_NAMES",
        "OpenCodeAdapterError", "ROLE_NAMES", "_UNSUPPORTED_MODEL_OVERRIDE", "role_contracts",
    )},
    "install_assets": "opencode_adapter_assets",
    **{name: "opencode_adapter_models" for name in (
        "_configured_model", "issue_number_from_arguments", "model_mappings_from_config",
        "reject_unsupported_model_overrides", "render_model_mappings", "resolve_opencode_model_mappings",
    )},
    **{name: "opencode_adapter_storage" for name in (
        "_file_sha256", "_read_diagnostics", "_read_json", "_read_state", "_read_text",
        "_write_diagnostics", "_write_json", "_write_text",
    )},
    **{name: "opencode_adapter_handoff" for name in (
        "_bounded_reader_bundle", "_bounded_result", "_bounded_text", "_fixer_source",
        "_next_semantic_attempt", "_plan_text", "_prepare_reader", "_prepare_synthesizer",
        "_write_plan_template", "build_planner_prompt_from_area_reader",
    )},
    **{name: "opencode_adapter_protocol" for name in (
        "_begin_role_invocation", "_contract_output_path", "_ensure_opencode_protocol",
        "_mark_role_accepted", "_reset_current_correction", "_resolved_policies",
        "_write_role_contracts", "ensure_current_issue",
    )},
    **{name: "opencode_adapter_roles" for name in (
        "_accept_role_once", "_raise_contract_rejection", "accept_role", "prepare_role",
    )},
    "workflow_stage": "opencode_adapter_workflow",
    **{name: "opencode_adapter_cli" for name in ("build_parser", "main", "run")},
}

LEGACY_PRODUCTION = [
    "opencode_adapter.py",
    "run_real_issue.py",
    "run_real_issue_core.py",
    "prepare_planner_prompt.py",
    "issue_runner_artifacts.py",
    "issue_runner_commands.py",
    "issue_runner_config.py",
    "issue_runner_contract.py",
    "issue_runner_implementation.py",
    "issue_runner_legacy.py",
    "issue_runner_prompts.py",
    "issue_runner_pull_request.py",
    "issue_runner_reader.py",
    "issue_runner_repository.py",
    "issue_runner_storage.py",
    "issue_runner_verification.py",
    "issue_run_checkpoints.py",
    "issue_run_entrypoint.py",
    "issue_run_implementation.py",
    "issue_run_models.py",
    "issue_run_pull_request.py",
    "issue_run_repository.py",
    "issue_run_resume.py",
    "issue_run_runtime.py",
    "issue_run_semantic.py",
    "issue_run_session.py",
]
LEGACY_TESTS = ["test_run_real_issue.py", "test_run_resume.py"]
LEGACY_SCRIPTS = ["run-real-issue.sh", "run-real-issue.ps1"]


def append_planner_prompt_ownership() -> None:
    path = AUTOMATION / "opencode_adapter_handoff.py"
    text = path.read_text(encoding="utf-8")
    if "def build_planner_prompt_from_area_reader(" in text:
        return
    if "import json\n" not in text:
        text = text.replace("import hashlib\n", "import hashlib\nimport json\n")
    addition = r'''


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
'''
    path.write_text((text.rstrip() + addition).rstrip() + "\n", encoding="utf-8")


def retarget_planner_role() -> None:
    path = AUTOMATION / "opencode_adapter_roles.py"
    text = path.read_text(encoding="utf-8")
    text = text.replace("from automation import run_real_issue_core as run_core\n", "")
    if "build_planner_prompt_from_area_reader," not in text:
        text = text.replace(
            "from automation.opencode_adapter_handoff import (\n",
            "from automation.opencode_adapter_handoff import (\n    build_planner_prompt_from_area_reader,\n",
        )
    text = text.replace("run_core.build_planner_prompt_from_area_reader(", "build_planner_prompt_from_area_reader(")
    path.write_text(text, encoding="utf-8")


def remove_exact_adapter_import(text: str) -> str:
    text = text.replace("    opencode_adapter,\n", "")
    text = text.replace("from automation import opencode_adapter\n", "")
    text = re.sub(r"from automation import opencode_adapter,\s*", "from automation import ", text)
    text = re.sub(r",\s*opencode_adapter(?=\s*(?:,|\n))", "", text)
    return text


def retarget_adapter_consumers() -> None:
    for path in [*AUTOMATION.rglob("*.py"), *TESTS.rglob("*.py")]:
        if path.name == "opencode_adapter.py":
            continue
        text = path.read_text(encoding="utf-8")
        attrs = sorted(set(re.findall(r"\bopencode_adapter\.([A-Za-z_]\w*)", text)))
        if not attrs and not re.search(r"(?:from automation import .*\bopencode_adapter\b|\bimport automation\.opencode_adapter\b)", text, re.S):
            continue
        owners: set[str] = set()
        for attr in attrs:
            owner = ATTRIBUTE_OWNER.get(attr)
            if owner is None:
                raise SystemExit(f"unknown opencode_adapter attribute {attr!r} in {path.relative_to(ROOT)}")
            owners.add(owner)
            text = text.replace(f"opencode_adapter.{attr}", f"{owner}.{attr}")
        text = remove_exact_adapter_import(text)
        for owner in sorted(owners):
            statement = f"from automation import {owner}\n"
            if statement not in text:
                insertion = text.find("\n", text.find("from __future__ import annotations")) + 1
                text = text[:insertion] + "\n" + statement + text[insertion:]
        if re.search(r"\bopencode_adapter\.", text):
            raise SystemExit(f"adapter facade reference remains in {path.relative_to(ROOT)}")
        path.write_text(text, encoding="utf-8")


def patch_ci() -> None:
    path = ROOT / ".github/workflows/ci.yml"
    text = path.read_text(encoding="utf-8")
    old = '''      - name: Run mocked issue-to-PR smoke tests\n        run: >-\n          python -m unittest -v\n          tests.test_run_real_issue.RunRealIssueTests.test_plan_only_uses_reader_provider_for_planning_not_coder_provider\n          tests.test_run_real_issue.RunRealIssueTests.test_dry_run_implementation_calls_coder_and_saves_patch\n'''
    new = '''      - name: Run canonical AutoDev CLI smoke tests\n        run: >-\n          python -m unittest -v\n          tests.test_autodev_cli.AutoDevCliTests.test_existing_commands_share_opencode_entrypoint_core\n          tests.test_role_runtime.RuntimeAgnosticCoordinatorTests.test_mock_runtime_executes_reader_synthesizer_planner_through_same_coordinator\n'''
    if old not in text:
        raise SystemExit("legacy issue-runner CI smoke block not found")
    path.write_text(text.replace(old, new), encoding="utf-8")


def delete_legacy_surface() -> None:
    for name in LEGACY_PRODUCTION:
        (AUTOMATION / name).unlink(missing_ok=True)
    for name in LEGACY_TESTS:
        (TESTS / name).unlink(missing_ok=True)
    for name in LEGACY_SCRIPTS:
        (ROOT / "scripts" / name).unlink(missing_ok=True)
    (ROOT / "docs" / "run-real-issue.md").unlink(missing_ok=True)


def guard() -> None:
    forbidden = (
        "run_real_issue", "issue_runner_", "issue_run_", "opencode_adapter.",
        "scripts/run-real-issue", "docs/run-real-issue",
    )
    offenders: list[str] = []
    for parent in (AUTOMATION, TESTS, ROOT / "docs", ROOT / "scripts"):
        if not parent.exists():
            continue
        for path in parent.rglob("*"):
            if not path.is_file() or path.suffix not in {".py", ".md", ".sh", ".ps1"}:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if any(token in text for token in forbidden):
                offenders.append(str(path.relative_to(ROOT)))
    if offenders:
        raise SystemExit("legacy runner/adapter references remain: " + ", ".join(sorted(set(offenders))))
    if (AUTOMATION / "opencode_adapter.py").exists():
        raise SystemExit("opencode_adapter facade still exists")


def main() -> None:
    append_planner_prompt_ownership()
    retarget_planner_role()
    retarget_adapter_consumers()
    patch_ci()
    delete_legacy_surface()
    guard()


if __name__ == "__main__":
    main()
