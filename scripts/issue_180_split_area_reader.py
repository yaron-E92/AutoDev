from __future__ import annotations

import ast
import functools
import inspect
from collections import defaultdict
from pathlib import Path


SOURCE = Path("area_reader_v2/runner_core.py")
PACKAGE = Path("area_reader_v2")

GROUPS: dict[str, set[str]] = {
    "area_reader_settings": {
        "REPO_TOOL_ROOT",
        "OLLAMA_CHAT_URL",
        "DEFAULT_MAX_CHARS_PER_AREA",
        "DEFAULT_READER_NUM_PREDICT",
        "DEFAULT_SYNTH_NUM_PREDICT",
        "DEFAULT_CODER_NUM_PREDICT",
        "MAX_FILE_BYTES",
        "PREFERRED_SOLUTION_FILTER_MARKERS",
        "MARKDOWN_SMOKE_SCRIPT",
        "SUPPORTED_AREAS",
        "DEFAULT_AUTO_AREAS",
        "INCLUDED_SUFFIXES",
        "INCLUDED_FILENAMES",
        "EXCLUDED_DIRS",
        "PRIORITY_PATTERNS",
        "AREA_HINTS",
    },
    "area_reader_cli": {"parse_args", "expand_user_path"},
    "area_reader_storage": {"write_text", "write_json", "write_executable_text"},
    "area_reader_repository": {
        "is_included_file",
        "iter_candidate_files",
        "matches_any",
        "is_priority_file",
        "area_for_file",
        "collect_repo_files",
        "build_repo_map",
        "read_json_object",
        "xml_local_name",
        "read_csproj_facts",
        "package_root",
        "package_manager_for_root",
        "detect_repo_facts",
    },
    "area_reader_verification": {
        "command",
        "command_group",
        "script_command_for_package",
        "preferred_solution_filter",
        "dotnet_solution_targets",
        "build_verification_command_groups",
        "detect_android_sdk_available",
        "recommended_command_groups",
        "apply_recommended_command_groups",
        "shell_function_name",
        "render_verification_script",
    },
    "area_reader_routing": {"route_areas", "area_file_map", "format_area_file_map"},
    "area_reader_context": {"read_file_for_bundle", "build_area_bundle"},
    "area_reader_prompts": {"build_area_reader_prompt", "build_synthesis_prompt", "build_coder_prompt"},
    "area_reader_provider": {
        "call_ollama",
        "duration_seconds",
        "tokens_per_sec",
        "extract_message",
        "build_metrics",
        "model_config_from_args",
        "call_provider",
    },
    "area_reader_execution": {"run_area_reader"},
    "area_reader_workflow": {"main"},
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


def import_bindings(node: ast.AST) -> set[str]:
    if isinstance(node, ast.Import):
        return {
            alias.asname or alias.name.split(".", 1)[0]
            for alias in node.names
        }
    if isinstance(node, ast.ImportFrom):
        return {
            alias.asname or alias.name
            for alias in node.names
            if alias.name != "*"
        }
    return set()


def source_imports(lines: list[str], tree: ast.Module) -> str:
    return "\n".join(
        segment(lines, node).rstrip()
        for node in tree.body
        if isinstance(node, (ast.Import, ast.ImportFrom))
        and not (isinstance(node, ast.ImportFrom) and node.module == "__future__")
    ) + "\n"


def selective_imports(lines: list[str], tree: ast.Module, nodes: list[ast.AST]) -> str:
    used = {
        item.id
        for node in nodes
        for item in ast.walk(node)
        if isinstance(item, ast.Name) and isinstance(item.ctx, ast.Load)
    }
    selected: list[str] = []
    for node in tree.body:
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        if isinstance(node, ast.ImportFrom) and node.module == "__future__":
            continue
        if import_bindings(node) & used:
            selected.append(segment(lines, node).rstrip())
    return "\n".join(selected) + ("\n" if selected else "")


def loaded(node: ast.AST, known: set[str]) -> set[str]:
    return {
        item.id
        for item in ast.walk(node)
        if isinstance(item, ast.Name)
        and isinstance(item.ctx, ast.Load)
        and item.id in known
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


def split() -> tuple[dict[str, list[str]], str]:
    text = SOURCE.read_text(encoding="utf-8")
    if "_COMPAT_ORIGINALS" in text and all((PACKAGE / f"{module}.py").is_file() for module in GROUPS):
        print("area reader is already split; preserving committed modular sources")
        return {}, ""

    lines = text.splitlines(keepends=True)
    tree = ast.parse(text)
    owner = {name: module for module, names in GROUPS.items() for name in names}
    if len(owner) != sum(len(names) for names in GROUPS.values()):
        raise SystemExit("duplicate definition assignment in area-reader groups")

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
            raise SystemExit("unassigned definitions in area reader: " + ", ".join(missing))
        if len(modules) != 1:
            raise SystemExit(f"one definition spans area-reader groups: {sorted(names)}")
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
        raise SystemExit("assigned area-reader definitions missing from source: " + ", ".join(missing))

    deps = {module: set(imports[module]) for module in GROUPS}
    assert_acyclic(deps)

    exports: dict[str, list[str]] = {}
    for module in GROUPS:
        module_nodes = nodes[module]
        parts = ["from __future__ import annotations\n\n"]
        parts.append(selective_imports(lines, tree, module_nodes))
        if parts[-1]:
            parts.append("\n")
        for dep_module, names in sorted(imports[module].items()):
            rendered = ",\n    ".join(sorted(names))
            parts.append(f"from area_reader_v2.{dep_module} import (\n    {rendered},\n)\n")
        parts.append("\n")
        exported: list[str] = []
        for node in module_nodes:
            parts.append(segment(lines, node).rstrip() + "\n\n")
            exported.extend(node_names(node))
        target = PACKAGE / f"{module}.py"
        target.write_text("".join(parts).rstrip() + "\n", encoding="utf-8")
        exports[module] = sorted(set(exported))
        print(f"wrote {target} ({len(target.read_text(encoding='utf-8').splitlines())} lines)")

    return exports, source_imports(lines, tree)


def facade_imports(exports: dict[str, list[str]]) -> str:
    chunks: list[str] = []
    for module, names in exports.items():
        rendered = ",\n    ".join(names)
        chunks.append(f"from area_reader_v2.{module} import (\n    {rendered},\n)\n")
    return "\n".join(chunks)


def compatibility_block(module_aliases: list[str]) -> str:
    modules = ",\n    ".join(module_aliases)
    return f'''_COMPAT_MODULES = (\n    {modules},\n)\n_COMPAT_MISSING = object()\n_COMPAT_ORIGINALS = {{\n    module: {{\n        name: value\n        for name, value in module.__dict__.items()\n        if name in globals() and not name.startswith("__")\n    }}\n    for module in _COMPAT_MODULES\n}}\n_COMPAT_BASELINE: dict[str, object] = {{}}\n\n\ndef _sync_compat_overrides() -> None:\n    facade = globals()\n    for module, originals in _COMPAT_ORIGINALS.items():\n        namespace = module.__dict__\n        for name, original in originals.items():\n            current = facade.get(name, _COMPAT_MISSING)\n            if current is _COMPAT_MISSING:\n                continue\n            baseline = _COMPAT_BASELINE.get(name, _COMPAT_MISSING)\n            namespace[name] = original if current is baseline else current\n\n\ndef _compat_entrypoint(target):\n    @functools.wraps(target)\n    def invoke(*args, **kwargs):\n        _sync_compat_overrides()\n        return target(*args, **kwargs)\n    return invoke\n\n\ndef _install_compat_entrypoints() -> None:\n    facade = globals()\n    wrapped: set[str] = set()\n    for module in _COMPAT_MODULES:\n        for name in tuple(module.__dict__):\n            if name in wrapped or name.startswith("__") or name not in facade:\n                continue\n            value = facade[name]\n            if inspect.isfunction(value) and value.__module__.startswith("area_reader_v2."):\n                facade[name] = _compat_entrypoint(value)\n                wrapped.add(name)\n\n\n_install_compat_entrypoints()\n_COMPAT_BASELINE.update(globals())\n'''


def write_facade(exports: dict[str, list[str]], original_imports: str) -> None:
    aliases = [f"_m{i}" for i in range(len(exports))]
    module_imports = "\n".join(
        f"from area_reader_v2 import {module} as {alias}"
        for alias, module in zip(aliases, exports)
    )
    text = f'''#!/usr/bin/env python3\n"""Compatibility facade for the responsibility-based area-reader pipeline."""\n\nfrom __future__ import annotations\n\nimport functools\nimport inspect\nimport sys\nfrom pathlib import Path\n\nREPO_TOOL_ROOT = Path(__file__).resolve().parents[1]\nif str(REPO_TOOL_ROOT) not in sys.path:\n    sys.path.insert(0, str(REPO_TOOL_ROOT))\n\n{original_imports}\n{module_imports}\n\n{facade_imports(exports)}\n\n{compatibility_block(aliases)}\n\nif __name__ == "__main__":\n    raise SystemExit(main())\n'''
    SOURCE.write_text(text, encoding="utf-8")
    print(f"wrote {SOURCE} ({len(text.splitlines())} lines)")


def main() -> None:
    exports, original_imports = split()
    if not exports:
        return
    write_facade(exports, original_imports)


if __name__ == "__main__":
    main()
