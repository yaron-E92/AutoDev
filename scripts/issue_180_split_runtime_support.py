from __future__ import annotations

import argparse
import ast
import functools
import inspect
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SplitSpec:
    source: Path
    groups: dict[str, set[str]]
    entrypoint: str = ""


SPECS: dict[str, SplitSpec] = {
    "repair-budget": SplitSpec(
        Path("automation/semantic_repair_budget.py"),
        {
            "repair_budget_contract": {
                "FAILURE_REPAIR_BUDGET_EXHAUSTED", "ROOT_FAILURE_CLASSIFICATION", "FORMULA_VERSION",
                "POLICY_ENV", "FIXED_LIMIT_ENV", "ADAPTIVE_MIN_ENV", "ADAPTIVE_MAX_ENV", "ADAPTIVE_BASE_ENV",
                "LINES_PER_ATTEMPT_ENV", "DEFAULT_ADAPTIVE_MIN", "DEFAULT_ADAPTIVE_MAX", "DEFAULT_ADAPTIVE_BASE",
                "DEFAULT_LINES_PER_ATTEMPT", "_GENERATED_PREFIXES", "_BINARY_SUFFIXES", "SemanticRepairBudgetError",
            },
            "repair_budget_metrics": {"change_metrics", "_changed_lines", "_line_count", "_generated", "_path_weight"},
            "repair_budget_policy": {
                "validate_config", "resolve_budget", "_resume_budget", "_policy", "_nonnegative_int", "_positive_int",
            },
            "repair_budget_failure": {"failure_details", "concise_failure_reason", "human_failure_summary"},
            "repair_budget_storage": {"persist_budget", "persist_failure", "clear_failure_state", "_read_json", "_write_json"},
            "repair_budget_manifest": {"install_run_manifest_hooks"},
            "repair_budget_resume": {
                "maybe_reopen_exhausted_budget", "install_opencode_resume_hooks", "_append_resume_metadata", "_status_metadata",
            },
        },
    ),
    "queue": SplitSpec(
        Path("automation/issue_queue.py"),
        {
            "queue_contract": {
                "MANAGED_LABEL", "READY_LABEL", "BLOCKED_LABEL", "ATTENTION_LABEL", "RUNNING_LABEL", "QUEUE_CONFIG",
                "API_VERSION", "DEFAULT_LIMIT", "LABEL_SPECS", "QueueError", "QueuePolicy", "QueueIssue", "Blocker",
                "QueueState", "CommandResult", "_label_names", "_milestone_title",
            },
            "queue_policy": {"load_policy"},
            "queue_github": {
                "_run_gh", "_json_result", "resolve_github_repo", "_queue_issue", "list_issues", "fetch_issue",
                "list_blockers", "remove_dependency", "ensure_queue_labels",
            },
            "queue_classification": {"_split_blockers", "classify_issue", "_desired_derived_labels", "_update_derived_labels"},
            "queue_workflow": {"inspect_queue", "reconcile_queue"},
            "queue_presentation": {"queue_summary", "explain_state", "_state_json"},
            "queue_cli": {"_parser", "run_cli"},
        },
        entrypoint="run_cli",
    ),
    "providers": SplitSpec(
        Path("automation/model_providers.py"),
        {
            "provider_contract": {
                "PROVIDER_ALIASES", "SUPPORTED_PROVIDERS", "SAFE_HEADER_NAMES", "SENSITIVE_HEADER_NAMES", "ProviderError",
                "ProviderResponse", "ModelConfig", "ModelProvider",
            },
            "provider_requests": {
                "build_chat_completions_body", "build_responses_body", "validated_request_options", "apply_model_selection",
                "apply_free_only_routing", "validate_output_limit", "validate_safe_headers", "classify_http_status",
                "http_failure_message", "response_telemetry",
            },
            "provider_command": {"CommandProvider", "quote_shell_argument"},
            "provider_http": {"_OpenAICompatibleProvider", "ChatCompletionsProvider", "ResponsesProvider"},
            "provider_headroom": {"HeadroomProvider", "headroom_role_from_prompt", "with_headroom_role"},
            "provider_mock": {"MockProvider"},
            "provider_factory": {
                "normalize_provider_name", "ollama_command_for_model", "create_provider", "load_provider_config",
                "model_config_from_values", "object_map", "object_string_map", "resolve_model_config",
            },
        },
    ),
    "resume": SplitSpec(
        Path("automation/opencode_resume.py"),
        {
            "opencode_resume_contract": {
                "ROLE_NAMES", "MODEL_STAGE_ROLE", "REPAIR_STAGE_KIND", "NEXT_ACTION", "OpenCodeResumeError",
                "manifest_path", "has_manifest",
            },
            "opencode_resume_manifest": {"create_open_code_manifest", "role_snapshots", "reconcile_models"},
            "opencode_resume_checkpoint": {
                "begin_role", "checkpoint_role", "checkpoint_stage", "checkpoint_failure", "_record_incomplete_stage",
                "_checkpoint_patch_applied", "_source_details", "_existing", "_stage_record", "_stage_output_hash",
                "_stage_attempt", "_repair_kind", "_stage_for_repair_kind",
            },
            "opencode_resume_status": {
                "status_text", "resume_action", "repair_attempts", "_resume_problems", "_changed_role_consequences",
                "_role_for_action",
            },
            "opencode_resume_execution": {"resume", "_repair_atomic_implementation_checkpoint"},
        },
    ),
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
        start = min(start, *(int(item.lineno) for item in decorators))
    return start


def segment(lines: list[str], node: ast.AST) -> str:
    start = node_start_line(node) - 1
    end = int(getattr(node, "end_lineno", start + 1))
    return "".join(lines[start:end]).rstrip() + "\n"


def import_bindings(node: ast.AST) -> set[str]:
    if isinstance(node, ast.Import):
        return {alias.asname or alias.name.split(".", 1)[0] for alias in node.names}
    if isinstance(node, ast.ImportFrom):
        return {alias.asname or alias.name for alias in node.names if alias.name != "*"}
    return set()


def loaded_names(nodes: list[ast.AST]) -> set[str]:
    return {
        item.id
        for node in nodes
        for item in ast.walk(node)
        if isinstance(item, ast.Name) and isinstance(item.ctx, ast.Load)
    }


def selective_imports(lines: list[str], tree: ast.Module, nodes: list[ast.AST]) -> str:
    used = loaded_names(nodes)
    selected: list[str] = []
    for node in tree.body:
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        if isinstance(node, ast.ImportFrom) and node.module == "__future__":
            continue
        if import_bindings(node) & used:
            selected.append(segment(lines, node).rstrip())
    return "\n".join(selected) + ("\n" if selected else "")


def original_imports(lines: list[str], tree: ast.Module) -> str:
    return "\n".join(
        segment(lines, node).rstrip()
        for node in tree.body
        if isinstance(node, (ast.Import, ast.ImportFrom))
        and not (isinstance(node, ast.ImportFrom) and node.module == "__future__")
    ) + "\n"


def assert_acyclic(deps: dict[str, set[str]]) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(module: str, trail: list[str]) -> None:
        if module in visited:
            return
        if module in visiting:
            raise SystemExit("split dependency cycle: " + " -> ".join([*trail, module]))
        visiting.add(module)
        for dep in sorted(deps.get(module, ())):
            visit(dep, [*trail, module])
        visiting.remove(module)
        visited.add(module)

    for module in deps:
        visit(module, [])


def compatibility_block(aliases: list[str]) -> str:
    modules = ",\n    ".join(aliases)
    return f'''_COMPAT_MODULES = (\n    {modules},\n)\n_COMPAT_MISSING = object()\n_COMPAT_ORIGINALS = dict(\n    (module, dict(\n        (name, value)\n        for name, value in module.__dict__.items()\n        if name in globals() and not name.startswith("__")\n    ))\n    for module in _COMPAT_MODULES\n)\n_COMPAT_BASELINE: dict[str, object] = {{}}\n\n\ndef _sync_compat_overrides() -> None:\n    facade = globals()\n    for module, originals in _COMPAT_ORIGINALS.items():\n        namespace = module.__dict__\n        for name, original in originals.items():\n            current = facade.get(name, _COMPAT_MISSING)\n            if current is _COMPAT_MISSING:\n                continue\n            baseline = _COMPAT_BASELINE.get(name, _COMPAT_MISSING)\n            namespace[name] = original if current is baseline else current\n\n\ndef _compat_entrypoint(target):\n    @functools.wraps(target)\n    def invoke(*args, **kwargs):\n        _sync_compat_overrides()\n        return target(*args, **kwargs)\n    return invoke\n\n\ndef _install_compat_entrypoints() -> None:\n    facade = globals()\n    wrapped: set[str] = set()\n    for module in _COMPAT_MODULES:\n        for name in tuple(module.__dict__):\n            if name in wrapped or name.startswith("__") or name not in facade:\n                continue\n            value = facade[name]\n            if inspect.isfunction(value) and value.__module__.startswith("automation."):\n                facade[name] = _compat_entrypoint(value)\n                wrapped.add(name)\n\n\n_install_compat_entrypoints()\n_COMPAT_BASELINE.update(globals())\n'''


def split(spec: SplitSpec) -> None:
    source = spec.source
    text = source.read_text(encoding="utf-8")
    if "_COMPAT_ORIGINALS" in text and all((source.parent / f"{module}.py").is_file() for module in spec.groups):
        print(f"{source} already split")
        return

    lines = text.splitlines(keepends=True)
    tree = ast.parse(text)
    owner = {name: module for module, names in spec.groups.items() for name in names}
    if len(owner) != sum(len(names) for names in spec.groups.values()):
        raise SystemExit(f"duplicate split assignments for {source}")

    nodes: dict[str, list[ast.AST]] = defaultdict(list)
    cross: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    known = set(owner)
    seen: set[str] = set()
    for node in tree.body:
        names = node_names(node)
        if not names:
            continue
        modules = {owner.get(name) for name in names}
        if None in modules:
            missing = sorted(name for name in names if name not in owner)
            raise SystemExit(f"unassigned definitions in {source}: {', '.join(missing)}")
        if len(modules) != 1:
            raise SystemExit(f"one node spans split groups in {source}: {sorted(names)}")
        module = modules.pop()
        assert module is not None
        nodes[module].append(node)
        seen.update(names)
        for dep in (loaded_names([node]) & known) - names:
            dep_module = owner[dep]
            if dep_module != module:
                cross[module][dep_module].add(dep)

    missing = sorted(known - seen)
    if missing:
        raise SystemExit(f"assigned definitions missing from {source}: {', '.join(missing)}")
    assert_acyclic({module: set(cross[module]) for module in spec.groups})

    exports: dict[str, list[str]] = {}
    for module in spec.groups:
        module_nodes = nodes[module]
        parts = ["from __future__ import annotations\n\n", selective_imports(lines, tree, module_nodes), "\n"]
        for dep_module, names in sorted(cross[module].items()):
            rendered = ",\n    ".join(sorted(names))
            parts.append(f"from automation.{dep_module} import (\n    {rendered},\n)\n")
        parts.append("\n")
        exported = sorted({name for node in module_nodes for name in node_names(node)})
        exports[module] = exported
        for node in module_nodes:
            parts.append(segment(lines, node).rstrip() + "\n\n")
        target = source.parent / f"{module}.py"
        target.write_text("".join(parts).rstrip() + "\n", encoding="utf-8")
        print(f"wrote {target} ({len(target.read_text(encoding='utf-8').splitlines())} lines)")

    aliases = [f"_m{i}" for i in range(len(exports))]
    module_imports = "\n".join(
        f"from automation import {module} as {alias}"
        for alias, module in zip(aliases, exports)
    )
    facade_imports = "\n\n".join(
        f"from automation.{module} import (\n    " + ",\n    ".join(names) + "\n)"
        for module, names in exports.items()
    )
    facade = f'''from __future__ import annotations\n\nimport functools\nimport inspect\n\n{original_imports(lines, tree)}\n{module_imports}\n\n{facade_imports}\n\n{compatibility_block(aliases)}\n'''
    if spec.entrypoint:
        facade += f'\nif __name__ == "__main__":\n    raise SystemExit({spec.entrypoint}())\n'
    source.write_text(facade.rstrip() + "\n", encoding="utf-8")
    print(f"wrote facade {source} ({len(facade.splitlines())} lines)")


def rewrite_queue_selection() -> None:
    path = Path("automation/queue_selection.py")
    text = path.read_text(encoding="utf-8")
    old = "from automation import issue_queue, opencode_resume, run_manifest, workflow_stages\n"
    new = '''from automation import opencode_resume, run_manifest, workflow_stages\nfrom automation.queue_contract import DEFAULT_LIMIT, QueueError, QueueIssue, QueueState\nfrom automation.queue_github import _json_result, _run_gh\nfrom automation.queue_workflow import inspect_queue, reconcile_queue\n'''
    if old in text:
        text = text.replace(old, new, 1)
    replacements = {
        "issue_queue.QueueError": "QueueError",
        "issue_queue._run_gh": "_run_gh",
        "issue_queue.DEFAULT_LIMIT": "DEFAULT_LIMIT",
        "issue_queue._json_result": "_json_result",
        "issue_queue.QueueIssue": "QueueIssue",
        "issue_queue.QueueState": "QueueState",
        "issue_queue.inspect_queue": "inspect_queue",
        "issue_queue.reconcile_queue": "reconcile_queue",
    }
    for before, after in replacements.items():
        text = text.replace(before, after)
    if "issue_queue." in text:
        raise SystemExit("queue_selection still depends on the issue_queue facade")
    path.write_text(text, encoding="utf-8")
    print("rewrote automation/queue_selection.py to depend on queue layers directly")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("target", choices=tuple(SPECS))
    args = parser.parse_args()
    split(SPECS[args.target])
    if args.target == "queue":
        rewrite_queue_selection()


if __name__ == "__main__":
    main()
