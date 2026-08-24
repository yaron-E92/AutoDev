from __future__ import annotations

import ast
import re
from collections import defaultdict, deque
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_ROOTS = (ROOT / "automation", ROOT / "area_reader")
CANONICAL_ROOTS = {"automation.autodev_cli"}
TEXT_SUFFIXES = {".py", ".sh", ".ps1", ".md", ".yml", ".yaml", ".json", ".jsonc", ".env", ".service", ".timer", ".toml"}
OPERATIONAL_PREFIXES = (".github/", "integrations/", "linux/", "windows/", "scripts/")


def production_files() -> list[Path]:
    return sorted(path for root in PRODUCTION_ROOTS for path in root.rglob("*.py"))


def module_name(path: Path) -> str:
    rel = path.relative_to(ROOT).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def imported_modules(path: Path, known: set[str]) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in known:
                    out.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            if node.module in known:
                out.add(node.module)
            for alias in node.names:
                candidate = f"{node.module}.{alias.name}"
                if candidate in known:
                    out.add(candidate)
    return out


def reachable(graph: dict[str, set[str]], roots: set[str]) -> set[str]:
    seen: set[str] = set()
    queue = deque(root for root in roots if root in graph)
    while queue:
        item = queue.popleft()
        if item in seen:
            continue
        seen.add(item)
        queue.extend(graph[item] - seen)
    return seen


def text_files() -> list[Path]:
    return sorted(
        path
        for path in ROOT.rglob("*")
        if path.is_file()
        and ".git" not in path.parts
        and (path.suffix.lower() in TEXT_SUFFIXES or path.name in {"README", "Makefile"})
        and path.relative_to(ROOT).as_posix() != "scripts/issue_181_usage_audit.py"
    )


def operational_module_roots(files: list[Path], known: set[str]) -> tuple[set[str], dict[str, list[str]]]:
    roots: set[str] = set()
    evidence: dict[str, list[str]] = defaultdict(list)
    module_pattern = re.compile(r"\b(?:automation|area_reader)(?:\.[A-Za-z_][A-Za-z0-9_]*)+")
    for path in files:
        rel = path.relative_to(ROOT).as_posix()
        if not rel.startswith(OPERATIONAL_PREFIXES):
            continue
        if rel.startswith("scripts/issue_181_") or rel.startswith(".github/workflows/issue-181-"):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for match in module_pattern.findall(text):
            candidate = match
            while candidate and candidate not in known and "." in candidate:
                candidate = candidate.rsplit(".", 1)[0]
            if candidate in known:
                roots.add(candidate)
                evidence[candidate].append(rel)
    return roots, evidence


def reverse_text_refs(files: list[Path], target: Path) -> tuple[list[str], list[str]]:
    rel = target.relative_to(ROOT).as_posix()
    base = target.name
    stem = target.stem
    needles = {rel, base}
    if target.suffix == ".py":
        needles.add(module_name(target))
        needles.add(stem)
    all_refs: list[str] = []
    runtime_refs: list[str] = []
    for path in files:
        other = path.relative_to(ROOT).as_posix()
        if path == target or other.startswith("scripts/issue_181_") or other.startswith(".github/workflows/issue-181-"):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if any(needle and needle in text for needle in needles):
            all_refs.append(other)
            if not other.startswith(("docs/", "tests/")) and not other.endswith("README.md") and other not in {"README.md", "CONTRIBUTING.md"}:
                runtime_refs.append(other)
    return sorted(set(all_refs)), sorted(set(runtime_refs))


def main() -> int:
    prod = production_files()
    known = {module_name(path) for path in prod}
    by_name = {module_name(path): path for path in prod}
    graph = {name: imported_modules(path, known) - {name} for name, path in by_name.items()}
    files = text_files()
    external_roots, root_evidence = operational_module_roots(files, known)
    canonical = reachable(graph, CANONICAL_ROOTS)
    operational_roots = CANONICAL_ROOTS | external_roots
    operational = reachable(graph, operational_roots)

    print("=== CANONICAL ROOTS ===")
    for item in sorted(CANONICAL_ROOTS):
        print(item)
    print("=== EXPLICIT OPERATIONAL PYTHON ROOTS ===")
    for item in sorted(external_roots):
        print(f"{item}: {', '.join(sorted(set(root_evidence[item])))}")
    print("=== PYTHON UNREACHABLE FROM CANONICAL + OPERATIONAL ROOTS ===")
    unreachable = sorted(known - operational)
    for item in unreachable:
        refs, runtime_refs = reverse_text_refs(files, by_name[item])
        print(f"{item} | runtime_refs={runtime_refs} | all_refs={refs}")

    candidates: list[Path] = []
    for prefix in ("linux", "windows", "scripts", "ollama-aliases"):
        root = ROOT / prefix
        if root.exists():
            candidates.extend(
                path for path in root.rglob("*")
                if path.is_file() and path.relative_to(ROOT).as_posix() != "scripts/issue_181_usage_audit.py"
            )
    candidates.extend(path for path in (ROOT / "tests" / "fixtures" / "eval").rglob("*") if path.is_file())
    candidates.extend(by_name[item] for item in unreachable)

    print("=== FILE CANDIDATE REVERSE REFERENCES ===")
    for target in sorted(set(candidates)):
        refs, runtime_refs = reverse_text_refs(files, target)
        rel = target.relative_to(ROOT).as_posix()
        print(f"{rel} | runtime_refs={runtime_refs} | all_refs={refs}")

    print("=== ZERO-RUNTIME-REF CANDIDATES ===")
    for target in sorted(set(candidates)):
        refs, runtime_refs = reverse_text_refs(files, target)
        if not runtime_refs:
            rel = target.relative_to(ROOT).as_posix()
            print(f"{rel} | all_refs={refs}")

    print(f"canonical_reachable={len(canonical)} operational_reachable={len(operational)} production_modules={len(known)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
