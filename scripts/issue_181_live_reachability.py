from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ("automation", "area_reader")


def module_name(path: Path) -> str:
    rel = path.relative_to(ROOT).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


modules = {
    module_name(path): path
    for package in PACKAGES
    for path in (ROOT / package).rglob("*.py")
    if "__pycache__" not in path.parts
}
edges: dict[str, set[str]] = {name: set() for name in modules}
for name, path in modules.items():
    tree = ast.parse(path.read_text(encoding="utf-8"))
    package = name.rsplit(".", 1)[0] if "." in name else name
    for node in ast.walk(tree):
        candidates: list[str] = []
        if isinstance(node, ast.Import):
            candidates.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                parts = package.split(".")
                keep = max(0, len(parts) - node.level + 1)
                prefix = ".".join(parts[:keep])
                base = ".".join(part for part in (prefix, base) if part)
            if base:
                candidates.append(base)
                candidates.extend(f"{base}.{alias.name}" for alias in node.names)
        for candidate in candidates:
            probe = candidate
            while probe:
                if probe in modules:
                    edges[name].add(probe)
                    break
                probe = probe.rpartition(".")[0]

roots = {"automation.autodev_cli"}
pattern = re.compile(r"(?:python(?:3)?\s+-m\s+|from\s+|import\s+)(automation|area_reader)\.([A-Za-z0-9_\.]+)")
scan_roots = (ROOT / ".github", ROOT / "scripts", ROOT / "integrations")
for scan_root in scan_roots:
    if not scan_root.exists():
        continue
    for path in scan_root.rglob("*"):
        if not path.is_file() or path == Path(__file__):
            continue
        if path.suffix.lower() not in {".yml", ".yaml", ".sh", ".ps1", ".cmd", ".py", ".toml", ".json"}:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in pattern.finditer(text):
            candidate = f"{match.group(1)}.{match.group(2).rstrip('.')}"
            probe = candidate
            while probe:
                if probe in modules:
                    roots.add(probe)
                    break
                probe = probe.rpartition(".")[0]

reachable: set[str] = set()
stack = [root for root in roots if root in modules]
while stack:
    item = stack.pop()
    if item in reachable:
        continue
    reachable.add(item)
    stack.extend(edges.get(item, ()))

print("LIVE ROOTS")
for item in sorted(roots):
    print(item)
print("\nUNREACHABLE FROM LIVE ROOTS")
for item in sorted(set(modules) - reachable):
    print(item)
print(f"\nTOTAL={len(modules)} REACHABLE={len(reachable)} UNREACHABLE={len(set(modules)-reachable)}")
