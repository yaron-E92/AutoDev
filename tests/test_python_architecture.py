from __future__ import annotations

from automation import headroom

import ast
import importlib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_ROOTS = (ROOT / "automation", ROOT / "area_reader")
MAX_MODULE_LINES = 700
REPRESENTATIVE_MODULES = (
    "automation.scheduler_registration",
    "automation.workflow_dispatch",
    "automation.semantic_invocation",
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
    "automation.planner_output",
)
REMOVED_PATHS = (
    "automation/run_real_issue.py",
    "automation/run_real_issue_core.py",
    "automation/workflow_stages_core.py",
    "automation/semantic_verifier.py",
    "automation/windows_verification.py",
    "automation/opencode_resume.py",
    "automation/opencode_adapter.py",
    "automation/role_coordinator.py",
    "automation/opencode_coordinator.py",
    "automation/eval_harness.py",
    "automation/eval_harness_core.py",
    "automation/create_issues_from_description.py",
    "automation/ollama_cloud_preflight.py",
    "automation/workflow_stage_legacy.py",
    "automation/workflow_verify_current.py",
    "integrations/opencode/autodev.py",
    "integrations/opencode/autodev.ps1",
    "integrations/opencode/python-commands",
    "linux",
    "windows/scripts/issue-to-pr-cycle.ps1",
    "windows/scripts/codex-common.ps1",
    "windows/scripts/codex-finalize-current-issue.ps1",
    "windows/scripts/codex-mark-current-issue.ps1",
    "windows/scripts/codex-plan-current-issue.ps1",
    "windows/scripts/codex-prepare-next-ready-issue.ps1",
    "windows/scripts/ensure-codex-labels.ps1",
    "area_reader_v2",
    "area_reader/cli.py",
    "area_reader/execution.py",
    "area_reader/pipeline.py",
    "area_reader/provider.py",
    "area_reader/storage.py",
    "area_reader/workflow.py",
    "automation/semantic_cli.py",
    "automation/prompt_runner.py",
)
MAINTAINED_DOCS = (
    "CONTRIBUTING.md",
    "docs/headroom.md",
    "docs/model-roles.md",
    "docs/opencode.md",
    "docs/python-architecture.md",
)
STALE_TEXT_MARKERS = (
    "automation." + "run_real_issue",
    "automation." + "semantic_verifier",
    "automation." + "windows_verification.py",
    "automation." + "opencode_resume.py",
    "workflow_stages" + "_core.py",
    "eval_harness" + "_core.py",
    "area_reader" + "_v2",
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

    def test_representative_responsibility_modules_import_cleanly(self):
        for name in REPRESENTATIVE_MODULES:
            with self.subTest(module=name):
                importlib.import_module(name)

    def test_removed_legacy_module_paths_do_not_return(self):
        existing = [relative for relative in REMOVED_PATHS if (ROOT / relative).exists()]
        self.assertEqual(existing, [])

    def test_maintained_docs_do_not_reference_retired_python_surfaces(self):
        stale: list[str] = []
        for relative in MAINTAINED_DOCS:
            text = (ROOT / relative).read_text(encoding="utf-8")
            for marker in STALE_TEXT_MARKERS:
                if marker in text:
                    stale.append(f"{relative}: {marker}")
        self.assertEqual(stale, [])

    def test_issue_180_migration_scaffolding_is_not_shipped(self):
        leftovers: list[str] = []
        scripts = ROOT / "scripts"
        workflows = ROOT / ".github" / "workflows"
        if scripts.is_dir():
            leftovers.extend(
                path.relative_to(ROOT).as_posix()
                for path in scripts.glob("issue_180_*")
            )
        if workflows.is_dir():
            leftovers.extend(
                path.relative_to(ROOT).as_posix()
                for path in workflows.glob("issue-180-*")
            )
        chunks = sorted(
            path.relative_to(ROOT).as_posix()
            for path in ROOT.rglob("*.chunk*.txt")
            if ".git" not in path.parts
        )
        self.assertEqual(sorted(leftovers), [])
        self.assertEqual(chunks, [])


if __name__ == "__main__":
    unittest.main()
