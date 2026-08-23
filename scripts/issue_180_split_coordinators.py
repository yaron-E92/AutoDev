from __future__ import annotations

import argparse
import ast
import functools
import inspect
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path


SHARED_CONSTANTS = {
    "ROLE_PROMPT",
    "CORRECTION_PROMPT",
    "REPAIR_KINDS",
    "ROLE_ACTIONS",
    "ROLE_TIMEOUT_SECONDS",
    "MAX_TRANSITIONS",
}


@dataclass(frozen=True)
class CoordinatorSpec:
    source: Path
    prefix: str
    error_name: str
    groups: dict[str, set[str]]


SPECS: dict[str, CoordinatorSpec] = {
    "generic": CoordinatorSpec(
        source=Path("automation/role_coordinator.py"),
        prefix="role_coord",
        error_name="RoleCoordinatorError",
        groups={
            "role_coord_contract": {
                "ROLE_PROMPT", "CORRECTION_PROMPT", "REPAIR_KINDS", "ROLE_ACTIONS", "ROLE_TIMEOUT_SECONDS",
                "ROLE_TIMEOUT_ENV", "LEGACY_ROLE_TIMEOUT_ENV", "MAX_TRANSITIONS", "RoleCoordinatorError",
                "role_timeout_seconds", "RoleResumeErrorAlias",
            },
            "role_coord_state": {"_issue_number", "role_acceptance", "_role_output_path", "_prepare_role"},
            "role_coord_runtime": {"_accept_role", "_record_attempt", "_runtime_failure", "_invoke", "run_role"},
            "role_coord_stages": {"run_stage", "terminal_payload", "_resume_payload"},
            "role_coord_flow": {"coordinate"},
            "role_coord_cli": {"invalidations", "run", "main"},
        },
    ),
    "opencode": CoordinatorSpec(
        source=Path("automation/opencode_coordinator.py"),
        prefix="opencode_coord",
        error_name="OpenCodeCoordinatorError",
        groups={
            "opencode_coord_contract": {
                "ROLE_PROMPT", "CORRECTION_PROMPT", "REPAIR_KINDS", "ROLE_ACTIONS", "ROLE_TIMEOUT_SECONDS",
                "ROLE_TIMEOUT_ENV", "MAX_TRANSITIONS", "OpenCodeCoordinatorError", "role_timeout_seconds",
            },
            "opencode_coord_state": {"_issue_number", "role_acceptance", "_role_output_path", "_prepare_role"},
            "opencode_coord_runtime": {"_record_runtime_failure", "_run_agent_process", "_record_validated_attempt", "run_role"},
            "opencode_coord_stages": {"run_stage", "terminal_payload", "_resume_payload"},
            "opencode_coord_flow": {"coordinate"},
            "opencode_coord_cli": {"invalidations", "run", "main"},
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
            raise SystemExit("coordinator split dependency cycle: " + " -> ".join([*trail, module]))
        visiting.add(module)
        for dep in sorted(deps.get(module, ())):
            visit(dep, [*trail, module])
        visiting.remove(module)
        visited.add(module)

    for module in deps:
        visit(module, [])


def shared_render(name: str, spec: CoordinatorSpec) -> str | None:
    if name in SHARED_CONSTANTS:
        return f"{name} = coordination_contract.{name}\n"
    if name == "_issue_number":
        return '''def _issue_number(repo: Path, arguments: str = "") -> int:\n    return coordination_state.issue_number(repo, arguments)\n'''
    if name == "role_acceptance":
        return '''def role_acceptance(repo: Path, role: str) -> dict[str, object]:\n    return coordination_state.role_acceptance(repo, role)\n'''
    if name == "_role_output_path":
        return '''def _role_output_path(repo: Path, role: str) -> Path | None:\n    return coordination_state.role_output_path(repo, role)\n'''
    if name == "invalidations":
        return f'''def invalidations(arguments: str) -> set[str]:\n    return coordination_state.invalidated_roles(\n        arguments,\n        roles=tuple(opencode_adapter.ROLE_NAMES),\n        error_type={spec.error_name},\n    )\n'''
    return None


def render_node(lines: list[str], node: ast.AST, spec: CoordinatorSpec) -> str:
    names = node_names(node)
    if len(names) == 1:
        replacement = shared_render(next(iter(names)), spec)
        if replacement is not None:
            return replacement
    return segment(lines, node)


def compatibility_block(aliases: list[str]) -> str:
    modules = ",\n    ".join(aliases)
    return f'''_COMPAT_MODULES = (\n    {modules},\n)\n_COMPAT_MISSING = object()\n_COMPAT_ORIGINALS = dict(\n    (module, dict(\n        (name, value)\n        for name, value in module.__dict__.items()\n        if name in globals() and not name.startswith("__")\n    ))\n    for module in _COMPAT_MODULES\n)\n_COMPAT_BASELINE: dict[str, object] = {{}}\n\n\ndef _sync_compat_overrides() -> None:\n    facade = globals()\n    for module, originals in _COMPAT_ORIGINALS.items():\n        namespace = module.__dict__\n        for name, original in originals.items():\n            current = facade.get(name, _COMPAT_MISSING)\n            if current is _COMPAT_MISSING:\n                continue\n            baseline = _COMPAT_BASELINE.get(name, _COMPAT_MISSING)\n            namespace[name] = original if current is baseline else current\n\n\ndef _compat_entrypoint(target):\n    @functools.wraps(target)\n    def invoke(*args, **kwargs):\n        _sync_compat_overrides()\n        return target(*args, **kwargs)\n    return invoke\n\n\ndef _install_compat_entrypoints() -> None:\n    facade = globals()\n    wrapped: set[str] = set()\n    for module in _COMPAT_MODULES:\n        for name in tuple(module.__dict__):\n            if name in wrapped or name.startswith("__") or name not in facade:\n                continue\n            value = facade[name]\n            if inspect.isfunction(value) and value.__module__.startswith("automation."):\n                facade[name] = _compat_entrypoint(value)\n                wrapped.add(name)\n\n\n_install_compat_entrypoints()\n_COMPAT_BASELINE.update(globals())\n'''


def split(spec: CoordinatorSpec) -> None:
    source = spec.source
    text = source.read_text(encoding="utf-8")
    if "_COMPAT_ORIGINALS" in text and all((source.parent / f"{module}.py").is_file() for module in spec.groups):
        print(f"{source} already split")
        return

    lines = text.splitlines(keepends=True)
    tree = ast.parse(text)
    owner = {name: module for module, names in spec.groups.items() for name in names}
    if len(owner) != sum(len(names) for names in spec.groups.values()):
        raise SystemExit(f"duplicate coordinator split assignments for {source}")

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
            raise SystemExit(f"unassigned coordinator definitions in {source}: {', '.join(missing)}")
        if len(modules) != 1:
            raise SystemExit(f"one coordinator node spans groups in {source}: {sorted(names)}")
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
        raise SystemExit(f"assigned coordinator definitions missing from {source}: {', '.join(missing)}")
    assert_acyclic({module: set(cross[module]) for module in spec.groups})

    exports: dict[str, list[str]] = {}
    for module in spec.groups:
        module_nodes = nodes[module]
        parts = [
            "from __future__ import annotations\n\n",
            selective_imports(lines, tree, module_nodes),
            "from automation import coordination_contract, coordination_state\n\n",
        ]
        for dep_module, names in sorted(cross[module].items()):
            rendered = ",\n    ".join(sorted(names))
            parts.append(f"from automation.{dep_module} import (\n    {rendered},\n)\n")
        parts.append("\n")
        exported = sorted({name for node in module_nodes for name in node_names(node)})
        exports[module] = exported
        for node in module_nodes:
            parts.append(render_node(lines, node, spec).rstrip() + "\n\n")
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
    facade = f'''from __future__ import annotations\n\nimport functools\nimport inspect\n\n{original_imports(lines, tree)}\n{module_imports}\n\n{facade_imports}\n\n{compatibility_block(aliases)}\n\nif __name__ == "__main__":\n    raise SystemExit(main())\n'''
    source.write_text(facade.rstrip() + "\n", encoding="utf-8")
    print(f"wrote facade {source} ({len(facade.splitlines())} lines)")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("target", choices=tuple(SPECS))
    args = parser.parse_args()
    split(SPECS[args.target])


if __name__ == "__main__":
    main()
