from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AUTOMATION = ROOT / "automation"
TESTS = ROOT / "tests"

FACADES: dict[str, dict[str, str]] = {
    "semantic_verifier": {
        **{name: "semantic_contract" for name in (
            "ALLOWED_FINDING_SEVERITIES", "ALLOWED_REQUIREMENT_STATUSES", "ALLOWED_VERDICTS",
            "ChangedFileList", "DEFAULT_MAX_REPAIR_ATTEMPTS", "DEFAULT_MAX_SCHEMA_RETRIES",
            "MAX_DIFF_CHARS", "MAX_EVIDENCE_CHARS", "MAX_REGRESSION_EVIDENCE_CHARS",
            "MAX_REGRESSION_FILE_BYTES", "MAX_REGRESSION_REFERENCES", "MAX_REGRESSION_SYMBOLS",
            "MAX_REPAIR_ATTEMPTS", "MAX_SCHEMA_RETRIES", "SEMANTIC_IGNORED_PARTS",
            "SEMANTIC_SOURCE_SUFFIXES", "SemanticSettings", "SemanticVerifierError",
            "_DECLARATION_PATTERNS", "_LEGACY_ONLY_PLACEHOLDERS", "_TEMPLATE_PLACEHOLDER",
        )},
        **{name: "semantic_configuration" for name in (
            "_bounded_count", "_config_error", "resolve_semantic_settings", "safe_semantic_metadata",
        )},
        **{name: "semantic_schema" for name in (
            "_malformed", "_parse_findings", "_parse_requirements", "_semantic_schema_errors",
            "parse_semantic_output", "semantic_result_template",
        )},
        **{name: "semantic_text" for name in ("_bounded", "render_template")},
        **{name: "semantic_prompts" for name in (
            "build_schema_repair_prompt", "build_semantic_prompt", "build_semantic_repair_prompt",
            "default_repair_template", "default_semantic_template", "extract_acceptance_criteria",
        )},
        **{name: "semantic_evidence" for name in (
            "_git_lines", "_git_text", "_is_tracked", "_removed_symbol_candidates",
            "collect_changed_files", "collect_cross_file_regression_evidence", "collect_current_diff",
            "collect_deterministic_evidence",
        )},
        **{name: "semantic_storage" for name in ("_read_json", "_read_text")},
        **{name: "semantic_artifacts" for name in (
            "_write_result_pair", "render_semantic_summary", "semantic_artifact_path",
            "write_final_verdict", "write_semantic_result",
        )},
        **{name: "semantic_invocation" for name in (
            "invoke_semantic_verifier", "prepare_semantic_prompt", "prepare_semantic_repair_prompt",
            "resolve_profile_roles",
        )},
        **{name: "semantic_cli" for name in ("build_parser", "run")},
    },
    "windows_verification": {
        **{name: "windows_verification_contract" for name in (
            "AUTODEV_ROOT", "CONFIG_PATH", "DEFAULT_CALLER_WORKFLOW", "DEFAULT_POLL_SECONDS",
            "DEFAULT_TIMEOUT_SECONDS", "FAILURE_CODE_REPAIRABLE", "FAILURE_DETERMINISTIC",
            "FAILURE_TRANSIENT", "MANIFEST_STAGE", "MAX_CAPTURE_CHARS", "REPAIR_FILE",
            "REQUEST_FILE", "RESULT_FILE", "SCHEMA_VERSION", "WindowsVerificationError",
            "_ACTIONS_NAME_PATTERN", "_COMMAND_MARKER", "_TRANSIENT_MARKERS", "utc_now",
        )},
        **{name: "windows_verification_storage" for name in (
            "_read_json", "_sha256_bytes", "_sha256_file", "_write_json", "_write_text",
        )},
        **{name: "windows_verification_process" for name in (
            "_json_stdout", "_returncode", "_run", "_stderr", "_stdout",
        )},
        **{name: "windows_verification_actions" for name in (
            "_current_autodev_ref", "_failed_logs", "_list_workflow_runs", "validate_actions_installation",
        )},
        **{name: "windows_verification_config" for name in (
            "load_config", "parse_deferred_obligations", "safe_config_metadata", "validate_config",
        )},
        **{name: "windows_verification_manifest" for name in (
            "_verification_head", "current_repair_attempt", "install_manifest_hooks", "payload_metadata",
            "proof_current", "sync_manifest", "windows_required",
        )},
        "record_local_deferred_obligations": "windows_verification_obligations",
        **{name: "windows_verification_failure" for name in (
            "_blocked_failure", "_infrastructure_failure", "_looks_transient_text", "_render_repair",
        )},
        **{name: "windows_verification_execution" for name in (
            "run_after_ci", "run_after_push", "validate_ready",
        )},
        "install_opencode_hooks": "windows_verification_hooks",
    },
    "opencode_resume": {
        **{name: "opencode_resume_contract" for name in (
            "MODEL_STAGE_ROLE", "NEXT_ACTION", "OpenCodeResumeError", "REPAIR_STAGE_KIND",
            "ROLE_NAMES", "has_manifest", "manifest_path",
        )},
        **{name: "opencode_resume_manifest" for name in (
            "create_open_code_manifest", "reconcile_models", "role_snapshots",
        )},
        **{name: "opencode_resume_checkpoint" for name in (
            "_checkpoint_patch_applied", "_existing", "_record_incomplete_stage", "_repair_kind",
            "_source_details", "_stage_attempt", "_stage_for_repair_kind", "_stage_output_hash",
            "_stage_record", "begin_role", "checkpoint_failure", "checkpoint_role", "checkpoint_stage",
        )},
        **{name: "opencode_resume_status" for name in (
            "_changed_role_consequences", "_resume_problems", "_role_for_action", "repair_attempts",
            "resume_action", "status_text",
        )},
        **{name: "opencode_resume_execution" for name in (
            "_repair_atomic_implementation_checkpoint", "resume",
        )},
    },
}


def owner(facade: str, name: str, path: Path) -> str:
    value = FACADES[facade].get(name)
    if value is None:
        raise SystemExit(
            f"unknown {facade} compatibility export {name!r} in {path.relative_to(ROOT)}"
        )
    return value


def direct_import_replacements(path: Path, source: str) -> str:
    tree = ast.parse(source)
    lines = source.splitlines(keepends=True)
    edits: list[tuple[int, int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or not node.module:
            continue
        facade = ""
        if node.module.startswith("automation."):
            candidate = node.module.split(".", 1)[1]
            if candidate in FACADES:
                facade = candidate
        if not facade:
            continue
        grouped: dict[str, list[ast.alias]] = defaultdict(list)
        for alias in node.names:
            grouped[owner(facade, alias.name, path)].append(alias)
        rendered: list[str] = []
        for module in sorted(grouped):
            names = ", ".join(
                alias.name + (f" as {alias.asname}" if alias.asname else "")
                for alias in grouped[module]
            )
            rendered.append(f"from automation.{module} import {names}\n")
        edits.append((node.lineno, node.end_lineno or node.lineno, "".join(rendered)))
    for start, end, replacement in sorted(edits, reverse=True):
        lines[start - 1 : end] = [replacement]
    return "".join(lines)


def retarget_module_attributes(path: Path, source: str) -> str:
    imported_facades: set[str] = set()
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise SystemExit(f"cannot parse {path.relative_to(ROOT)}: {exc}") from exc
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "automation":
            for alias in node.names:
                if alias.name in FACADES and not alias.asname:
                    imported_facades.add(alias.name)
    updated = source
    needed: set[str] = set()
    for facade in FACADES:
        prefix = facade + "."
        while prefix in updated:
            index = updated.find(prefix)
            start = index + len(prefix)
            end = start
            while end < len(updated) and (updated[end].isalnum() or updated[end] == "_"):
                end += 1
            name = updated[start:end]
            if not name:
                break
            module = owner(facade, name, path)
            updated = updated[:index] + f"{module}.{name}" + updated[end:]
            needed.add(module)
    if imported_facades:
        tree = ast.parse(updated)
        lines = updated.splitlines(keepends=True)
        edits: list[tuple[int, int, str]] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or node.module != "automation":
                continue
            if not any(alias.name in imported_facades for alias in node.names):
                continue
            remaining = [alias for alias in node.names if alias.name not in imported_facades]
            if remaining:
                names = ", ".join(
                    alias.name + (f" as {alias.asname}" if alias.asname else "")
                    for alias in remaining
                )
                replacement = f"from automation import {names}\n"
            else:
                replacement = ""
            edits.append((node.lineno, node.end_lineno or node.lineno, replacement))
        for start, end, replacement in sorted(edits, reverse=True):
            lines[start - 1 : end] = [replacement]
        updated = "".join(lines)
    for module in sorted(needed):
        statement = f"from automation import {module}\n"
        if statement not in updated:
            future = "from __future__ import annotations\n"
            if future in updated:
                updated = updated.replace(future, future + "\n" + statement, 1)
            else:
                updated = statement + updated
    return updated


def retarget(path: Path) -> None:
    source = path.read_text(encoding="utf-8")
    updated = direct_import_replacements(path, source)
    updated = retarget_module_attributes(path, updated)
    for facade in FACADES:
        if (
            f"from automation import {facade}" in updated
            or f"automation.{facade}" in updated
            or f"{facade}." in updated
        ):
            raise SystemExit(
                f"facade reference remains in {path.relative_to(ROOT)}: {facade}"
            )
    if updated != source:
        path.write_text(updated, encoding="utf-8")


def main() -> None:
    skip = {AUTOMATION / f"{name}.py" for name in FACADES}
    skip.add(Path(__file__).resolve())
    for parent in (AUTOMATION, TESTS):
        for path in sorted(parent.rglob("*.py")):
            if path.resolve() in {item.resolve() for item in skip}:
                continue
            retarget(path)
    for facade in FACADES:
        (AUTOMATION / f"{facade}.py").unlink(missing_ok=True)
    for facade in FACADES:
        if (AUTOMATION / f"{facade}.py").exists():
            raise SystemExit(f"compatibility facade still exists: {facade}.py")


if __name__ == "__main__":
    main()
