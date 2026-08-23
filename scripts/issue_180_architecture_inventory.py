from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(".")
EXCLUDED_PARTS = {
    ".git",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "tests",
}


def production_python_files() -> list[Path]:
    return sorted(
        path
        for path in ROOT.rglob("*.py")
        if not any(part in EXCLUDED_PARTS for part in path.parts)
    )


def module_name(path: Path) -> str:
    parts = list(path.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def top_level_names(tree: ast.Module) -> dict[str, ast.AST]:
    names: dict[str, ast.AST] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names[node.name] = node
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    names[target.id] = node
    return names


def local_references(node: ast.AST, known: set[str]) -> set[str]:
    return {
        item.id
        for item in ast.walk(node)
        if isinstance(item, ast.Name)
        and isinstance(item.ctx, ast.Load)
        and item.id in known
    }


def import_dependencies(tree: ast.Module, known: set[str]) -> set[str]:
    deps: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in known:
                    deps.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            if node.module in known:
                deps.add(node.module)
            for alias in node.names:
                candidate = f"{node.module}.{alias.name}"
                if candidate in known:
                    deps.add(candidate)
    return deps


def strongly_connected(graph: dict[str, set[str]]) -> list[list[str]]:
    index = 0
    stack: list[str] = []
    indices: dict[str, int] = {}
    lowlink: dict[str, int] = {}
    on_stack: set[str] = set()
    groups: list[list[str]] = []

    def visit(name: str) -> None:
        nonlocal index
        indices[name] = index
        lowlink[name] = index
        index += 1
        stack.append(name)
        on_stack.add(name)
        for dep in sorted(graph.get(name, ())):
            if dep not in indices:
                visit(dep)
                lowlink[name] = min(lowlink[name], lowlink[dep])
            elif dep in on_stack:
                lowlink[name] = min(lowlink[name], indices[dep])
        if lowlink[name] == indices[name]:
            group: list[str] = []
            while stack:
                item = stack.pop()
                on_stack.remove(item)
                group.append(item)
                if item == name:
                    break
            groups.append(sorted(group))

    for name in sorted(graph):
        if name not in indices:
            visit(name)
    return groups


def function_spans(tree: ast.Module) -> list[tuple[int, str, int, int]]:
    spans: list[tuple[int, str, int, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        start = int(getattr(node, "lineno", 0))
        end = int(getattr(node, "end_lineno", start))
        spans.append((end - start + 1, node.name, start, end))
    return sorted(spans, reverse=True)


def print_boundaries(path: Path, tree: ast.Module) -> None:
    definitions = top_level_names(tree)
    known = set(definitions)
    print(f"\n--- {path} responsibility boundaries ---")
    for name, node in sorted(definitions.items(), key=lambda item: getattr(item[1], "lineno", 0)):
        start = int(getattr(node, "lineno", 0))
        end = int(getattr(node, "end_lineno", start))
        refs = ",".join(sorted(local_references(node, known) - {name}))
        print(f"{start:4d}-{end:4d} {name} -> {refs}")


def main() -> None:
    files = production_python_files()
    parsed: dict[Path, ast.Module] = {}
    line_counts: dict[Path, int] = {}

    for path in files:
        text = path.read_text(encoding="utf-8")
        parsed[path] = ast.parse(text)
        line_counts[path] = len(text.splitlines())

    print("=== Python modules at or above 450 lines ===")
    large = sorted(
        ((count, path) for path, count in line_counts.items() if count >= 450),
        reverse=True,
    )
    if not large:
        print("none")
    for count, path in large:
        marker = " >700" if count > 700 else ""
        print(f"{count:5d}{marker:5s} {path}")

    print("\n=== Functions at or above 100 lines ===")
    long_functions: list[tuple[int, Path, str, int, int]] = []
    for path, tree in parsed.items():
        for length, name, start, end in function_spans(tree):
            if length >= 100:
                long_functions.append((length, path, name, start, end))
    if not long_functions:
        print("none")
    for length, path, name, start, end in sorted(long_functions, reverse=True):
        print(f"{length:4d} {path}:{start}-{end} {name}")

    names = {module_name(path) for path in files}
    by_name = {module_name(path): path for path in files}
    graph = {
        name: import_dependencies(parsed[path], names) - {name}
        for name, path in by_name.items()
    }
    cycles = [group for group in strongly_connected(graph) if len(group) > 1]
    print("\n=== Absolute local-import cycles ===")
    if not cycles:
        print("none")
    for group in cycles:
        members = set(group)
        print(" -> ".join(group))
        for source in group:
            for target in sorted(graph[source] & members):
                print(f"  {source} -> {target}")

    print("\n=== Remaining >700-line module boundaries ===")
    for count, path in large:
        if count > 700:
            print_boundaries(path, parsed[path])


if __name__ == "__main__":
    main()
