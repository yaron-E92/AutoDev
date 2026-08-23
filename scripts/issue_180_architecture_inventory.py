from __future__ import annotations

import ast
import sys
from collections import defaultdict
from pathlib import Path


TARGETS = [Path(value) for value in sys.argv[1:]] or [
    Path("automation/workflow_stages_core.py"),
    Path("automation/run_real_issue.py"),
    Path("automation/run_real_issue_core.py"),
    Path("area_reader_v2/runner_core.py"),
]


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


def references(node: ast.AST, known: set[str]) -> set[str]:
    return {
        item.id
        for item in ast.walk(node)
        if isinstance(item, ast.Name) and isinstance(item.ctx, ast.Load) and item.id in known
    }


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


def inspect(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    definitions = top_level_names(tree)
    known = set(definitions)
    graph = {
        name: references(node, known) - {name}
        for name, node in definitions.items()
    }
    print(f"\n===== {path} ({len(text.splitlines())} lines) =====")
    for name, node in sorted(definitions.items(), key=lambda item: getattr(item[1], "lineno", 0)):
        start = getattr(node, "lineno", 0)
        end = getattr(node, "end_lineno", start)
        deps = ",".join(sorted(graph[name]))
        print(f"DEF {start:4d}-{end:4d} {name} -> {deps}")
    cycles = [group for group in strongly_connected(graph) if len(group) > 1]
    print("CYCLES")
    if not cycles:
        print("  none")
    for group in cycles:
        print("  " + ", ".join(group))


for target in TARGETS:
    inspect(target)
