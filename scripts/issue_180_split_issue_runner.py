from __future__ import annotations

import ast
import functools
import inspect
from collections import defaultdict
from pathlib import Path


CORE = Path("automation/run_real_issue_core.py")
PUBLIC = Path("automation/run_real_issue.py")

CORE_GROUPS: dict[str, set[str]] = {
    "issue_runner_contract": {
        "DEFAULT_READER_MODEL", "DEFAULT_CODER_MODEL", "DEFAULT_READY_LABEL", "DEFAULT_RUNNING_LABEL",
        "DEFAULT_FAILED_LABEL", "DEFAULT_DONE_LABEL", "DEFAULT_BLOCKED_LABEL", "RUNNER_ROOT",
        "PROMPT_TEMPLATE_DIR", "FALLBACK_SYNTHESIZED_HANDOFF", "PATCH_START", "PATCH_END",
        "NO_CHANGES_REQUIRED", "CommandResult", "IssueSelection", "VerificationResult", "RunnerError",
    },
    "issue_runner_config": {
        "parse_args", "add_provider_args", "positive_int", "non_negative_int", "expand_path",
        "validate_inputs", "resolve_provider_configs", "default_ollama_command_config",
        "provider_cli_values", "add_default_ollama_command",
    },
    "issue_runner_commands": {"require_tools", "print_command", "run_command", "format_command_failure"},
    "issue_runner_repository": {
        "select_issue", "select_next_issue", "fetch_issue_text", "issue_text_from_json",
        "update_issue_labels", "ensure_clean_worktree", "issue_branch_name", "ensure_issue_branch",
    },
    "issue_runner_reader": {"run_area_reader", "append_provider_command_args"},
    "issue_runner_artifacts": {
        "write_operational_outputs", "refine_recommendations_for_plan_scope", "build_run_summary",
        "write_provider_metadata",
    },
    "issue_runner_implementation": {
        "run_implementation_loop", "call_coder", "process_model_response", "extract_unified_diff",
        "parse_no_changes_required", "apply_patch_file",
    },
    "issue_runner_verification": {
        "run_recommended_verification", "write_verification_attempt", "write_verification_result",
        "render_verification_summary",
    },
    "issue_runner_prompts": {
        "collect_area_reader_relevant_files", "collect_workspace_paths", "add_workspace_path",
        "workspace_snapshot_summary", "usable_synthesized_handoff", "synthesized_handoff_or_fallback",
        "planner_handoff_section", "build_area_reader_planner_prompt", "build_planner_prompt_from_area_reader",
        "build_implementation_prompt", "build_fix_prompt", "write_implementation_prompt_file", "current_diff",
    },
    "issue_runner_pull_request": {
        "create_draft_pr", "changed_worktree_paths", "is_relative_to", "build_pr_body", "first_issue_title",
    },
    "issue_runner_storage": {"read_json", "read_optional_text", "write_json", "write_text", "trim_log"},
    "issue_runner_legacy": {"run", "main"},
}

OVERLAY_GROUPS: dict[str, set[str]] = {
    "issue_run_session": {
        "_ACTIVE_ROLES", "_ACTIVE_FACTORY", "_ACTIVE_POLICIES", "_ACTIVE_SEMANTIC",
        "_ACTIVE_DEBUG_ARTIFACTS", "_ACTIVE_ARGS", "_ACTIVE_MANIFEST", "_ACTIVE_RESUMING",
        "_ACTIVE_ROLE_SNAPSHOTS", "_CORE_WRITE_OPERATIONAL_OUTPUTS", "_CORE_SELECT_ISSUE",
        "_CORE_FETCH_ISSUE_TEXT", "_CORE_ENSURE_CLEAN_WORKTREE", "_CORE_ENSURE_ISSUE_BRANCH",
        "_CORE_CREATE_DRAFT_PR", "_DeferredProvider", "_sync_manifest_invocations", "_active_args",
        "_active_manifest_path", "_active_manifest_data", "_stage_details", "_stage_output_hash",
        "_file_hash_or_empty", "_roles_or_legacy", "_policies_or_default", "_semantic_settings_or_disabled",
    },
    "issue_run_resume": {
        "_extract_resume_options", "_inject_resume_arguments", "_argument_value", "_build_role_snapshots",
        "_reconcile_semantic_settings", "_update_resume_target_options", "_provider_config_path",
        "_validate_next_stage_provider",
    },
    "issue_run_repository": {
        "update_issue_labels", "select_issue", "fetch_issue_text", "ensure_clean_worktree", "ensure_issue_branch",
        "_validate_resume_repository", "_pending_uncheckpointed_patch", "_patch_matches_resume_worktree",
        "_patch_paths", "_is_expected_autodev_commit",
    },
    "issue_run_runtime": {
        "resolve_role_provider_configs", "resolve_prompt_policy_configs", "resolve_semantic_verification_settings",
        "resolve_provider_configs", "run_area_reader", "write_operational_outputs",
        "_refresh_operational_checkpoints", "write_provider_metadata",
    },
    "issue_run_checkpoints": {
        "apply_patch_file", "_checkpoint_patch_applied", "_checkpoint_deterministic", "_pending_repair_patch",
        "_patch_is_recorded_as_applied", "_next_fix_attempt", "_resumed_verification",
        "_deterministic_matches_current_patch", "_clear_completed_stages", "_checkpoint_semantic",
    },
    "issue_run_models": {"call_coder", "_stage_for_model_role", "write_compression_debug_artifact"},
    "issue_run_semantic": {
        "run_semantic_verification_gate", "_run_final_semantic_attempt", "_invoke_semantic_attempt",
    },
    "issue_run_implementation": {"run_implementation_loop", "_run_uncheckpointed_implementation_loop"},
    "issue_run_pull_request": {"create_draft_pr", "_find_existing_pr", "_record_pr_checkpoint", "build_pr_body"},
    "issue_run_entrypoint": {"run", "main"},
}


def node_names(node: ast.AST) -> set[str]:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return {node.name}
    if isinstance(node, ast.Assign):
        return {target.id for target in node.targets if isinstance(target, ast.Name)}
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return {node.target.id}
    return set()


def node_start_line(node: ast.AST) -> int:
    start = int(getattr(node, "lineno", 1))
    decorators = getattr(node, "decorator_list", ())
    if decorators:
        start = min(start, *(int(decorator.lineno) for decorator in decorators))
    return start


def segment(lines: list[str], node: ast.AST) -> str:
    start = node_start_line(node) - 1
    end = int(getattr(node, "end_lineno", start + 1))
    return "".join(lines[start:end]).rstrip() + "\n"


def source_imports(lines: list[str], tree: ast.Module) -> str:
    result: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == "__future__":
            continue
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            result.append(segment(lines, node).rstrip())
    return "\n".join(result) + "\n"


def loaded(node: ast.AST, known: set[str]) -> set[str]:
    return {
        item.id for item in ast.walk(node)
        if isinstance(item, ast.Name) and isinstance(item.ctx, ast.Load) and item.id in known
    }


def assert_acyclic(deps: dict[str, set[str]]) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(module: str, trail: list[str]) -> None:
        if module in visited:
            return
        if module in visiting:
            raise SystemExit("module dependency cycle: " + " -> ".join([*trail, module]))
        visiting.add(module)
        for dep in sorted(deps.get(module, ())):
            visit(dep, [*trail, module])
        visiting.remove(module)
        visited.add(module)

    for module in deps:
        visit(module, [])


def split(
    path: Path,
    groups: dict[str, set[str]],
) -> tuple[dict[str, list[str]], dict[str, set[str]], str]:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    tree = ast.parse(text)
    owner = {name: module for module, names in groups.items() for name in names}
    if len(owner) != sum(len(names) for names in groups.values()):
        raise SystemExit(f"duplicate assignment in {path}")
    known = set(owner)
    nodes: dict[str, list[ast.AST]] = defaultdict(list)
    imports: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    seen: set[str] = set()
    for node in tree.body:
        names = node_names(node)
        if not names:
            continue
        modules = {owner.get(name) for name in names}
        if None in modules:
            missing = sorted(name for name in names if name not in owner)
            raise SystemExit(f"unassigned definitions in {path}: {', '.join(missing)}")
        if len(modules) != 1:
            raise SystemExit(f"one node spans groups in {path}: {sorted(names)}")
        module = modules.pop()
        assert module is not None
        nodes[module].append(node)
        seen.update(names)
        for dep in loaded(node, known) - names:
            dep_module = owner[dep]
            if dep_module != module:
                imports[module][dep_module].add(dep)
    missing = sorted(known - seen)
    if missing:
        raise SystemExit(f"assigned names missing from {path}: {', '.join(missing)}")
    deps = {module: set(imports[module]) for module in groups}
    assert_acyclic(deps)
    common = source_imports(lines, tree)
    exports: dict[str, list[str]] = {}
    for module in groups:
        parts = ["from __future__ import annotations\n\n", common]
        for dep_module, names in sorted(imports[module].items()):
            rendered = ",\n    ".join(sorted(names))
            parts.append(f"from automation.{dep_module} import (\n    {rendered},\n)\n")
        parts.append("\n")
        exported: list[str] = []
        for node in nodes[module]:
            parts.append(segment(lines, node).rstrip() + "\n\n")
            exported.extend(node_names(node))
        target = Path("automation") / f"{module}.py"
        target.write_text("".join(parts).rstrip() + "\n", encoding="utf-8")
        exports[module] = sorted(set(exported))
        print(f"wrote {target} ({len(target.read_text(encoding='utf-8').splitlines())} lines)")
    return exports, deps, common


def facade_imports(exports: dict[str, list[str]]) -> str:
    chunks: list[str] = []
    for module, names in exports.items():
        rendered = ",\n    ".join(names)
        chunks.append(f"from automation.{module} import (\n    {rendered},\n)\n")
    return "\n".join(chunks)


def compat_block(module_aliases: list[str]) -> str:
    modules = ",\n    ".join(module_aliases)
    return f'''_COMPAT_MODULES = (\n    {modules},\n)\n_COMPAT_MISSING = object()\n_COMPAT_ORIGINALS = {{\n    module: {{\n        name: value\n        for name, value in module.__dict__.items()\n        if name in globals() and not name.startswith("__")\n    }}\n    for module in _COMPAT_MODULES\n}}\n_COMPAT_BASELINE: dict[str, object] = {{}}\n\n\ndef _sync_compat_overrides() -> None:\n    facade = globals()\n    for module, originals in _COMPAT_ORIGINALS.items():\n        namespace = module.__dict__\n        for name, original in originals.items():\n            current = facade.get(name, _COMPAT_MISSING)\n            if current is _COMPAT_MISSING:\n                continue\n            baseline = _COMPAT_BASELINE.get(name, _COMPAT_MISSING)\n            namespace[name] = original if current is baseline else current\n\n\ndef _compat_entrypoint(target):\n    @functools.wraps(target)\n    def invoke(*args, **kwargs):\n        _sync_compat_overrides()\n        return target(*args, **kwargs)\n    return invoke\n\n\ndef _install_compat_entrypoints() -> None:\n    facade = globals()\n    wrapped: set[str] = set()\n    for module in _COMPAT_MODULES:\n        for name in tuple(module.__dict__):\n            if name in wrapped or name.startswith("__") or name not in facade:\n                continue\n            value = facade[name]\n            if inspect.isfunction(value) and value.__module__.startswith("automation."):\n                facade[name] = _compat_entrypoint(value)\n                wrapped.add(name)\n\n\n_install_compat_entrypoints()\n_COMPAT_BASELINE.update(globals())\n'''


def write_core_facade(exports: dict[str, list[str]], common_imports: str) -> None:
    aliases = [f"_m{i}" for i in range(len(exports))]
    module_imports = "\n".join(
        f"from automation import {module} as {alias}"
        for alias, module in zip(aliases, exports)
    )
    text = f'''from __future__ import annotations\n\nimport functools\nimport inspect\n\n{common_imports}\n{module_imports}\n\n{facade_imports(exports)}\n{compat_block(aliases)}\n\nif __name__ == "__main__":\n    raise SystemExit(main())\n'''
    CORE.write_text(text, encoding="utf-8")
    print(f"wrote {CORE} ({len(text.splitlines())} lines)")


def write_public_facade(exports: dict[str, list[str]], common_imports: str) -> None:
    aliases = [f"_overlay_{i}" for i in range(len(exports))]
    module_imports = "\n".join(
        f"from automation import {module} as {alias}"
        for alias, module in zip(aliases, exports)
    )
    text = f'''from __future__ import annotations\n\nimport functools\nimport inspect\n\n{common_imports}\n{module_imports}\n\n{facade_imports(exports)}\n{compat_block(["_core", *aliases])}\n\nif __name__ == "__main__":\n    raise SystemExit(main())\n'''
    PUBLIC.write_text(text, encoding="utf-8")
    print(f"wrote {PUBLIC} ({len(text.splitlines())} lines)")


def already_split() -> bool:
    required = [
        Path("automation/issue_runner_contract.py"),
        Path("automation/issue_runner_legacy.py"),
        Path("automation/issue_run_entrypoint.py"),
    ]
    if not all(path.exists() for path in required):
        return False
    return "_COMPAT_ORIGINALS" in CORE.read_text(encoding="utf-8") and "_COMPAT_ORIGINALS" in PUBLIC.read_text(encoding="utf-8")


def main() -> None:
    if already_split():
        print("issue runner is already split; preserving committed modular sources")
        return
    core_exports, _, core_imports = split(CORE, CORE_GROUPS)
    write_core_facade(core_exports, core_imports)
    overlay_exports, _, public_imports = split(PUBLIC, OVERLAY_GROUPS)
    write_public_facade(overlay_exports, public_imports)


if __name__ == "__main__":
    main()
