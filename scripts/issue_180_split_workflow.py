from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path


CORE = Path("automation/workflow_stages_core.py")
FACADE = Path("automation/workflow_stages.py")

GROUPS: dict[str, set[str]] = {
    "workflow_contract": {
        "AUTODEV_ROOT", "CURRENT_DIR", "DIAGNOSTICS_FILE", "VERIFICATION_PROOF_VERSION",
        "DEFAULT_CI_CHECK_POLL_ATTEMPTS", "DEFAULT_CI_CHECK_POLL_SECONDS", "STAGES",
        "DEFAULT_MAX_REPAIR_ATTEMPTS", "DEFAULT_MAX_SEMANTIC_REPAIR_ATTEMPTS",
        "FAILURE_CODE_REPAIRABLE", "FAILURE_TRANSIENT", "FAILURE_DETERMINISTIC",
        "IGNORED_PREFIXES", "WorkflowStageError", "issue_number_from_arguments",
        "configured_attempt_limit", "configured_nonnegative_float", "safe_slug", "concise",
        "_exception_classification",
    },
    "workflow_storage": {
        "read_state", "write_state", "read_json", "read_text", "write_json", "write_text",
        "_file_sha256", "_json_evidence",
    },
    "workflow_commands": {
        "gh", "git", "gh_json", "_run_captured", "_decoded_text", "_gh_environment",
        "_command_reason", "_command_failure_classification", "_porcelain_paths",
    },
    "workflow_workspace": {
        "validate_prepared_worktree", "source_identity", "workspace_changes", "_baseline_snapshot",
        "workspace_snapshot", "write_workspace_snapshot", "ignored_workspace_path", "repository_modified",
    },
    "workflow_prompts": {
        "resolve_profiles", "render_implementer_prompt", "render_ci_repair",
        "render_legacy_verifier", "commit_message",
    },
    "workflow_diagnostics": {
        "stage_payload", "record_stage_failure", "_require_accepted_role", "_repeat_failure_payload",
        "_stage_input_fingerprint", "_record_stage_invocation", "_record_stage_timing",
        "_record_shipment_diagnostics", "_diagnostics", "_write_diagnostics",
    },
    "workflow_github": {
        "create_api_commit", "ensure_pr", "wait_for_required_checks", "_query_pr_checks",
        "_ci_state", "_persist_ci_proof", "_pr_head_sha", "validate_ready_proof",
        "mark_ready", "mark_blocked",
    },
    "workflow_preparation": {"ensure_prepared_issue"},
    "workflow_verification": {"_preflight", "run_local_check", "pr_and_ci"},
    "workflow_dispatch": {"execute_stage", "_execute_stage_impl", "build_parser", "run", "main"},
}

SPECIAL_FACADE = {"execute_stage", "create_api_commit", "mark_blocked", "run", "main"}
WRAPPER_NAMES = {
    "_workspace_snapshot", "_workspace_file_paths", "_workspace_path_in_scope",
    "_resume_semantic_budget", "_create_api_commit", "_execute_stage", "_mark_blocked",
}


def node_names(node: ast.AST) -> set[str]:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return {node.name}
    if isinstance(node, ast.Assign):
        return {target.id for target in node.targets if isinstance(target, ast.Name)}
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return {node.target.id}
    return set()


def source_segment(lines: list[str], node: ast.AST) -> str:
    start = int(getattr(node, "lineno", 1)) - 1
    end = int(getattr(node, "end_lineno", start + 1))
    return "".join(lines[start:end]).rstrip() + "\n"


def loaded_names(node: ast.AST, known: set[str]) -> set[str]:
    return {
        item.id
        for item in ast.walk(node)
        if isinstance(item, ast.Name) and isinstance(item.ctx, ast.Load) and item.id in known
    }


def import_source(lines: list[str], tree: ast.Module) -> str:
    chunks: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == "__future__":
            continue
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            chunks.append(source_segment(lines, node))
    return "\n".join(chunk.rstrip() for chunk in chunks) + "\n"


def module_for_name() -> dict[str, str]:
    result: dict[str, str] = {}
    for module, names in GROUPS.items():
        for name in names:
            if name in result:
                raise SystemExit(f"duplicate assignment for {name}")
            result[name] = module
    return result


def assert_acyclic(module_deps: dict[str, set[str]]) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(module: str, trail: list[str]) -> None:
        if module in visited:
            return
        if module in visiting:
            raise SystemExit("module dependency cycle: " + " -> ".join([*trail, module]))
        visiting.add(module)
        for dep in sorted(module_deps.get(module, ())):
            visit(dep, [*trail, module])
        visiting.remove(module)
        visited.add(module)

    for module in GROUPS:
        visit(module, [])


def custom_workspace_snapshot() -> str:
    return '''def workspace_snapshot(repo: Path) -> dict[str, str]:
    try:
        return workspace_scope.workspace_snapshot(
            repo,
            fallback_ignored=ignored_workspace_path,
        )
    except workspace_scope.WorkspaceScopeError as exc:
        raise WorkflowStageError(str(exc)) from exc


def workspace_file_paths(repo: Path) -> list[str]:
    try:
        return workspace_scope.workspace_paths(
            repo,
            fallback_ignored=ignored_workspace_path,
        )
    except workspace_scope.WorkspaceScopeError as exc:
        raise WorkflowStageError(str(exc)) from exc


def workspace_path_in_scope(repo: Path, relative: str) -> bool:
    try:
        return workspace_scope.path_is_in_scope(
            repo,
            relative,
            fallback_ignored=ignored_workspace_path,
        )
    except workspace_scope.WorkspaceScopeError as exc:
        raise WorkflowStageError(str(exc)) from exc
'''


def split_core() -> tuple[dict[str, str], dict[str, list[str]]]:
    text = CORE.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    tree = ast.parse(text)
    name_to_module = module_for_name()
    known = set(name_to_module)

    nodes_by_module: dict[str, list[ast.AST]] = defaultdict(list)
    deps_by_module: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    seen: set[str] = set()

    for node in tree.body:
        names = node_names(node)
        if not names:
            continue
        assigned = {name_to_module.get(name) for name in names}
        if None in assigned:
            missing = sorted(name for name in names if name not in name_to_module)
            raise SystemExit("unassigned workflow definitions: " + ", ".join(missing))
        if len(assigned) != 1:
            raise SystemExit(f"one AST node spans groups: {sorted(names)}")
        module = assigned.pop()
        assert module is not None
        nodes_by_module[module].append(node)
        seen.update(names)
        for dep in loaded_names(node, known) - names:
            dep_module = name_to_module[dep]
            if dep_module != module:
                deps_by_module[module][dep_module].add(dep)

    missing = sorted(known - seen)
    if missing:
        raise SystemExit("assigned names not found in source: " + ", ".join(missing))

    module_deps = {module: set(deps) for module, deps in deps_by_module.items()}
    assert_acyclic(module_deps)

    common_imports = import_source(lines, tree)
    exports: dict[str, list[str]] = {}
    for module in GROUPS:
        pieces = ["from __future__ import annotations\n\n", common_imports]
        if module == "workflow_workspace":
            pieces.append("from automation import workspace_scope\n")
        for dep_module, names in sorted(deps_by_module[module].items()):
            rendered = ",\n    ".join(sorted(names))
            pieces.append(
                f"from automation.{dep_module} import (\n    {rendered},\n)\n"
            )
        pieces.append("\n")

        module_exports: list[str] = []
        for node in nodes_by_module[module]:
            names = sorted(node_names(node))
            if module == "workflow_workspace" and "workspace_snapshot" in names:
                pieces.append(custom_workspace_snapshot().rstrip() + "\n\n")
                module_exports.extend(["workspace_snapshot", "workspace_file_paths", "workspace_path_in_scope"])
                continue
            pieces.append(source_segment(lines, node).rstrip() + "\n\n")
            module_exports.extend(names)
        path = Path("automation") / f"{module}.py"
        path.write_text("".join(pieces).rstrip() + "\n", encoding="utf-8")
        exports[module] = sorted(set(module_exports))
        print(f"wrote {path} ({len(path.read_text(encoding='utf-8').splitlines())} lines)")
    return name_to_module, exports


def render_facade_imports(exports: dict[str, list[str]]) -> str:
    lines: list[str] = []
    for module in GROUPS:
        names = [name for name in exports[module] if name not in SPECIAL_FACADE]
        if module == "workflow_dispatch":
            names = [name for name in names if name != "execute_stage"]
        if not names:
            continue
        rendered = ",\n    ".join(names)
        lines.append(f"from automation.{module} import (\n    {rendered},\n)\n")
    return "\n".join(lines)


def facade_wrapper_source() -> str:
    text = FACADE.read_text(encoding="utf-8")
    tree = ast.parse(text)
    lines = text.splitlines(keepends=True)
    wrappers: list[str] = []
    found: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in WRAPPER_NAMES:
            segment = source_segment(lines, node).replace("_core.", "")
            wrappers.append(segment.rstrip() + "\n\n")
            found.add(node.name)
    missing = WRAPPER_NAMES - found
    if missing:
        raise SystemExit("missing facade wrappers: " + ", ".join(sorted(missing)))
    return "".join(wrappers)


def write_facade(exports: dict[str, list[str]]) -> None:
    imports = render_facade_imports(exports)
    wrappers = facade_wrapper_source()
    text = f'''from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from automation import semantic_repair_budget as _semantic_budget
from automation import windows_workflow_hooks as _windows_workflow_hooks
from automation import workflow_dispatch as _workflow_dispatch
from automation import workflow_github as _workflow_github
from automation import workflow_verification as _workflow_verification
from automation.semantic_verifier import SemanticVerifierError

{imports}
from automation.workflow_dispatch import execute_stage as _base_execute_stage
from automation.workflow_github import create_api_commit as _base_create_api_commit
from automation.workflow_github import mark_blocked as _base_mark_blocked

_original_create_api_commit = _base_create_api_commit
_original_execute_stage = _base_execute_stage
_original_mark_blocked = _base_mark_blocked

{wrappers}

workspace_snapshot = _workspace_snapshot
workspace_file_paths = _workspace_file_paths
workspace_path_in_scope = _workspace_path_in_scope
create_api_commit = _create_api_commit
execute_stage = _execute_stage
mark_blocked = _mark_blocked
FAILURE_REPAIR_BUDGET_EXHAUSTED = _semantic_budget.FAILURE_REPAIR_BUDGET_EXHAUSTED

# Explicitly install the cross-cutting compatibility boundaries in the modules
# that own the affected workflow operations. The dependency direction remains
# workflow_dispatch -> verification/github/workspace; the public facade only
# supplies policy wrappers at the edge.
_workflow_github.create_api_commit = create_api_commit
_workflow_verification.create_api_commit = create_api_commit
_workflow_github.mark_blocked = mark_blocked
_workflow_dispatch.mark_blocked = mark_blocked
_semantic_budget._resume_budget = _resume_semantic_budget  # type: ignore[attr-defined]
_semantic_budget.install_run_manifest_hooks()
_windows_workflow_hooks.install(sys.modules[__name__])


def run(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo = Path(args.repo).expanduser().resolve()
    try:
        code, payload = execute_stage(
            args.stage,
            repo,
            arguments=args.arguments,
            autodev_root=Path(args.autodev_root),
            attempt=args.attempt,
            reason=args.reason,
        )
    except (WorkflowStageError, SemanticVerifierError, OSError, ValueError) as exc:
        payload = record_stage_failure(
            repo,
            args.stage,
            exc,
            requested_issue=issue_number_from_arguments(args.arguments),
        )
        print(json.dumps(payload, sort_keys=True))
        return 1
    print(json.dumps(payload, sort_keys=True))
    return code


def main() -> int:
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
'''
    FACADE.write_text(text, encoding="utf-8")
    CORE.write_text(
        '''from __future__ import annotations\n\n# Compatibility import for historical internal references. New code must import\n# automation.workflow_stages or the responsibility-specific workflow modules.\nimport sys\nfrom automation import workflow_stages as _workflow_stages\n\nsys.modules[__name__] = _workflow_stages\n''',
        encoding="utf-8",
    )
    print(f"wrote {FACADE} ({len(text.splitlines())} lines)")
    print(f"wrote {CORE} compatibility shim")


def main() -> None:
    _mapping, exports = split_core()
    write_facade(exports)


if __name__ == "__main__":
    main()
