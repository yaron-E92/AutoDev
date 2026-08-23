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
    "windows": SplitSpec(
        Path("automation/windows_verification.py"),
        {
            "windows_verification_contract": {
                "AUTODEV_ROOT", "CONFIG_PATH", "DEFAULT_CALLER_WORKFLOW", "REQUEST_FILE", "RESULT_FILE", "REPAIR_FILE",
                "MANIFEST_STAGE", "SCHEMA_VERSION", "DEFAULT_TIMEOUT_SECONDS", "DEFAULT_POLL_SECONDS", "MAX_CAPTURE_CHARS",
                "FAILURE_CODE_REPAIRABLE", "FAILURE_TRANSIENT", "FAILURE_DETERMINISTIC", "_TRANSIENT_MARKERS",
                "_COMMAND_MARKER", "_ACTIONS_NAME_PATTERN", "WindowsVerificationError", "utc_now",
            },
            "windows_verification_storage": {"_sha256_bytes", "_sha256_file", "_write_json", "_write_text", "_read_json"},
            "windows_verification_process": {"_run", "_stdout", "_stderr", "_returncode", "_json_stdout"},
            "windows_verification_actions": {
                "_current_autodev_ref", "validate_actions_installation", "_list_workflow_runs", "_failed_logs",
            },
            "windows_verification_config": {
                "parse_deferred_obligations", "load_config", "validate_config", "safe_config_metadata",
            },
            "windows_verification_manifest": {
                "windows_required", "_verification_head", "proof_current", "current_repair_attempt", "payload_metadata",
                "sync_manifest", "install_manifest_hooks",
            },
            "windows_verification_obligations": {"record_local_deferred_obligations"},
            "windows_verification_failure": {
                "_looks_transient_text", "_blocked_failure", "_infrastructure_failure", "_render_repair",
            },
            "windows_verification_execution": {"run_after_push", "run_after_ci", "validate_ready"},
            "windows_verification_hooks": {"install_opencode_hooks"},
        },
    ),
    "adapter": SplitSpec(
        Path("automation/opencode_adapter.py"),
        {
            "opencode_adapter_contract": {
                "AUTODEV_ROOT", "CURRENT_DIR", "COMMAND_FILES", "AGENT_FILES", "ROLE_NAMES", "OPENCODE_ROLE_NAMES",
                "AUTODEV_AGENT_BY_ROLE", "COORDINATOR_STAGES", "MAX_HANDOFF_CHARS", "MAX_READER_BUNDLE_CHARS",
                "OPENCODE_PROTOCOL_VERSION", "DEFAULT_MAX_REPAIR_ATTEMPTS", "DEFAULT_MAX_SEMANTIC_REPAIR_ATTEMPTS",
                "_UNSUPPORTED_MODEL_OVERRIDE", "OpenCodeAdapterError", "role_contracts",
            },
            "opencode_adapter_assets": {"install_assets"},
            "opencode_adapter_models": {
                "issue_number_from_arguments", "reject_unsupported_model_overrides", "resolve_opencode_model_mappings",
                "model_mappings_from_config", "_configured_model", "render_model_mappings",
            },
            "opencode_adapter_storage": {
                "_read_diagnostics", "_write_diagnostics", "_file_sha256", "_read_state", "_read_json", "_read_text",
                "_write_text", "_write_json",
            },
            "opencode_adapter_handoff": {
                "_next_semantic_attempt", "_prepare_reader", "_bounded_reader_bundle", "_prepare_synthesizer", "_fixer_source",
                "_write_plan_template", "_plan_text", "_bounded_result", "_bounded_text",
            },
            "opencode_adapter_protocol": {
                "ensure_current_issue", "_ensure_opencode_protocol", "_write_role_contracts", "_begin_role_invocation",
                "_mark_role_accepted", "_reset_current_correction", "_contract_output_path", "_resolved_policies",
            },
            "opencode_adapter_roles": {"prepare_role", "accept_role", "_accept_role_once", "_raise_contract_rejection"},
            "opencode_adapter_workflow": {"workflow_stage"},
            "opencode_adapter_cli": {"build_parser", "run", "main"},
        },
        entrypoint="main",
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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("target", choices=tuple(SPECS))
    args = parser.parse_args()
    split(SPECS[args.target])


if __name__ == "__main__":
    main()
