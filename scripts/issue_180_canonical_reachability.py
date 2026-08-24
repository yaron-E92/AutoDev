from __future__ import annotations

import ast
import re
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIRS = [ROOT / "automation"]
for candidate in (ROOT / "area_reader", ROOT / "area_reader"):
    if candidate.is_dir():
        PACKAGE_DIRS.append(candidate)


def module_name(path: Path) -> str:
    relative = path.relative_to(ROOT).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


paths = [path for directory in PACKAGE_DIRS for path in directory.rglob("*.py") if "__pycache__" not in path.parts]
modules = {module_name(path): path for path in paths}
graph: dict[str, set[str]] = {name: set() for name in modules}

for name, path in modules.items():
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                candidate = alias.name
                while candidate:
                    if candidate in modules and candidate != name:
                        graph[name].add(candidate)
                        break
                    candidate = candidate.rpartition(".")[0]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            if node.module in modules and node.module != name:
                graph[name].add(node.module)
            for alias in node.names:
                candidate = f"{node.module}.{alias.name}"
                if candidate in modules and candidate != name:
                    graph[name].add(candidate)

roots = ["automation.autodev_cli"]
reachable: set[str] = set()
parent: dict[str, str] = {}
queue = deque(roots)
while queue:
    current = queue.popleft()
    if current in reachable or current not in modules:
        continue
    reachable.add(current)
    for dependency in sorted(graph[current] - reachable):
        parent.setdefault(dependency, current)
        queue.append(dependency)

print("=== CANONICAL ROOTS ===")
for root in roots:
    print(root)
print("=== REACHABLE MODULES ===")
for name in sorted(reachable):
    print(name)
print("=== UNREACHABLE MODULES ===")
for name in sorted(set(modules) - reachable):
    print(name)

print("=== SUSPICIOUS REACHABILITY PATHS ===")
interesting_prefixes = (
    "automation.opencode_coord",
    "automation.opencode_coordinator",
    "automation.role_coord",
    "automation.role_coordinator",
    "automation.run_real_issue",
    "automation.issue_runner",
    "automation.opencode_adapter",
    "automation.workflow_stages_core",
    "automation.workflow_stage_legacy",
    "area_reader",
)
for name in sorted(reachable):
    if not name.startswith(interesting_prefixes):
        continue
    chain = [name]
    cursor = name
    seen = {cursor}
    while cursor not in roots and cursor in parent:
        cursor = parent[cursor]
        if cursor in seen:
            break
        seen.add(cursor)
        chain.append(cursor)
    chain.reverse()
    print(" -> ".join(chain))

print("=== DIRECT REVERSE IMPORTERS ===")
for target in sorted(modules):
    if not target.startswith(interesting_prefixes):
        continue
    importers = sorted(name for name, deps in graph.items() if target in deps)
    if importers:
        print(f"{target}: {', '.join(importers)}")

print("=== NON-PYTHON PATH REFERENCES FROM REACHABLE MODULES ===")
path_pattern = re.compile(r"(?:scripts|linux|windows|integrations|\.github)/[A-Za-z0-9_./@{}$()\[\]-]+")
references: set[str] = set()
for name in sorted(reachable):
    text = modules[name].read_text(encoding="utf-8")
    for match in path_pattern.finditer(text):
        references.add(f"{name}: {match.group(0)}")
for item in sorted(references):
    print(item)

print("=== DIRECT PYTHON ENTRYPOINTS ===")
for name, path in sorted(modules.items()):
    text = path.read_text(encoding="utf-8")
    if 'if __name__ == "__main__"' in text or "if __name__ == '__main__'" in text:
        marker = "reachable" if name in reachable else "UNREACHABLE"
        print(f"{marker}: {name} ({path.relative_to(ROOT)})")
