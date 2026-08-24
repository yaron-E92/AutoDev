from __future__ import annotations

import ast
import importlib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_ROOTS = (ROOT / "automation", ROOT / "area_reader")
MAX_MODULE_LINES = 700
COMPATIBILITY_SHIMS = (
    ROOT / "automation" / "workflow_stages_core.py",
    ROOT / "automation" / "run_real_issue_core.py",
)
REPRESENTATIVE_MODULES = (
    "automation.scheduler_registration",
    "automation.workflow_dispatch",
    "automation.issue_run_entrypoint",
    "automation.semantic_invocation",
    "automation.evaluation_reporting",
    "automation.privacy_grant_hooks",
    "automation.claim_lease",
    "automation.scheduler_health_lifecycle",
    "automation.repair_budget_policy",
    "automation.queue_workflow",
    "automation.provider_factory",
    "automation.opencode_resume_execution",
    "automation.windows_verification_execution",
    "automation.opencode_adapter_workflow",
    "automation.coordination_state",
    "automation.role_coordinator_flow",
    "area_reader.pipeline",
)


def production_files() -> list[Path]:
    return sorted(
        path
        for root in PRODUCTION_ROOTS
        for path in root.rglob("*.py")
        if "__pycache__" not in path.parts
    )


def module_name(path: Path) -> str:
    relative = path.relative_to(ROOT).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def top_level_dependencies(path: Path, known: set[str]) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    dependencies: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in known:
                    dependencies.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            if node.module in known:
                dependencies.add(node.module)
            for alias in node.names:
                candidate = f"{node.module}.{alias.name}"
                if candidate in known:
                    dependencies.add(candidate)
    return dependencies


def first_cycle(graph: dict[str, set[str]]) -> list[str]:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(name: str, trail: list[str]) -> list[str]:
        if name in visited:
            return []
        if name in visiting:
            start = trail.index(name) if name in trail else 0
            return [*trail[start:], name]
        visiting.add(name)
        for dependency in sorted(graph[name]):
            cycle = visit(dependency, [*trail, name])
            if cycle:
                return cycle
        visiting.remove(name)
        visited.add(name)
        return []

    for name in sorted(graph):
        cycle = visit(name, [])
        if cycle:
            return cycle
    return []


class PythonArchitectureTests(unittest.TestCase):
    def test_production_modules_remain_below_giant_module_threshold(self):
        offenders = {
            path.relative_to(ROOT).as_posix(): len(path.read_text(encoding="utf-8").splitlines())
            for path in production_files()
            if len(path.read_text(encoding="utf-8").splitlines()) > MAX_MODULE_LINES
        }
        self.assertEqual(
            offenders,
            {},
            "Split responsibility modules before they exceed the 700-line architecture ceiling.",
        )

    def test_top_level_local_import_graph_is_acyclic(self):
        files = production_files()
        names = {module_name(path) for path in files}
        by_name = {module_name(path): path for path in files}
        graph = {
            name: top_level_dependencies(path, names) - {name}
            for name, path in by_name.items()
        }
        cycle = first_cycle(graph)
        self.assertEqual(cycle, [], "top-level local import cycle: " + " -> ".join(cycle))

    def test_legacy_core_files_are_compatibility_shims_not_dumping_grounds(self):
        offenders = {
            path.relative_to(ROOT).as_posix(): len(path.read_text(encoding="utf-8").splitlines())
            for path in COMPATIBILITY_SHIMS
            if path.is_file() and len(path.read_text(encoding="utf-8").splitlines()) > 300
        }
        self.assertEqual(offenders, {})

    def test_representative_responsibility_modules_import_cleanly(self):
        for name in REPRESENTATIVE_MODULES:
            with self.subTest(module=name):
                importlib.import_module(name)

    def test_issue_180_migration_scaffolding_is_not_shipped(self):
        leftovers = sorted(
            path.relative_to(ROOT).as_posix()
            for parent in (ROOT / "scripts", ROOT / ".github" / "workflows")
            if parent.is_dir()
            for path in parent.glob("issue-180-*")
        )
        chunks = sorted(
            path.relative_to(ROOT).as_posix()
            for path in ROOT.rglob("*.chunk*.txt")
            if ".git" not in path.parts
        )
        self.assertEqual(leftovers, [])
        self.assertEqual(chunks, [])


if __name__ == "__main__":
    unittest.main()
