from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_required(relative: str, old: str, new: str) -> None:
    path = ROOT / relative
    text = path.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"expected text not found in {relative}: {old[:80]!r}")
    path.write_text(text.replace(old, new), encoding="utf-8")


def regex_required(relative: str, pattern: str, replacement: str, *, count: int = 1) -> None:
    path = ROOT / relative
    text = path.read_text(encoding="utf-8")
    updated, matches = re.subn(pattern, replacement, text, count=count, flags=re.MULTILINE | re.DOTALL)
    if matches != count:
        raise SystemExit(f"expected {count} regex match(es) in {relative}, got {matches}: {pattern!r}")
    path.write_text(updated, encoding="utf-8")


replace_required(
    "CONTRIBUTING.md",
    "python -m unittest -v tests.test_run_real_issue.RunRealIssueTests.test_plan_only_uses_reader_provider_for_planning_not_coder_provider tests.test_run_real_issue.RunRealIssueTests.test_dry_run_implementation_calls_coder_and_saves_patch",
    "python -m unittest -v tests.test_autodev_cli.AutoDevCliTests.test_existing_commands_share_opencode_entrypoint_core tests.test_role_runtime.RuntimeAgnosticCoordinatorTests.test_mock_runtime_executes_reader_synthesizer_planner_through_same_coordinator",
)
replace_required(
    "CONTRIBUTING.md",
    "The two named `RunRealIssueTests` use `MockProvider`; they exercise plan-only reader routing and dry-run patch generation without contacting Ollama, GitHub, or a cloud model.",
    "The two named smoke tests exercise canonical CLI routing and the mock-runtime coordinator flow without contacting Ollama, GitHub, or a cloud model.",
)

replace_required(
    "docs/headroom.md",
    "With `automation.run_real_issue --debug-artifacts`, AutoDev also writes concise records beneath:",
    "When provider-backed execution enables debug artifacts, AutoDev also writes concise records beneath:",
)

regex_required(
    "docs/model-roles.md",
    r"Use the configuration with the operational runner:\n\n```text\npython -m automation\.run_real_issue \\\n.*?--out /tmp/autodev-run\n```",
    "Use the role configuration through the supported platform issue-to-PR entrypoints. The wrappers resolve the profile in Python and pass model work through the shared provider boundary; examples are shown under **Existing issue-to-PR entrypoints** below.",
)
regex_required(
    "docs/model-roles.md",
    r"### Use the profile\n\n```text\npython -m automation\.run_real_issue \\\n.*?--out /tmp/autodev-run\n```",
    "### Use the profile\n\nAfter the preflight succeeds, pass the same profile to the supported platform issue-to-PR entrypoints shown below. The profile remains the source of truth for role/provider selection during that run.",
)
replace_required(
    "docs/model-roles.md",
    "Semantic verification and Headroom compression are now independent optional layers above the shared role/provider transport. Resumable manifests and the evaluation harness remain separate issues.",
    "Semantic verification, resumable manifests, and Headroom compression are integrated layers above the shared role/provider transport.",
)

replace_required(
    "docs/opencode.md",
    "  -> provider-profile JSON used by automation.run_real_issue / prompt_runner\n  -> controls non-OpenCode/headless model transports",
    "  -> provider-profile JSON used by the platform workflows / automation.prompt_runner\n  -> controls non-OpenCode/headless model transports",
)
replace_required(
    "docs/opencode.md",
    "scripts/run-real-issue.ps1\nwindows/scripts/issue-to-pr-cycle.ps1\nlinux/scripts/issue-to-pr-cycle.sh\nautomation.prompt_runner\nautomation.run_real_issue",
    "scripts/run-real-issue.ps1\nwindows/scripts/issue-to-pr-cycle.ps1\nlinux/scripts/issue-to-pr-cycle.sh\nautomation.prompt_runner",
)

architecture_doc = '''# AutoDev Python architecture

AutoDev's Python implementation is organized around one-way responsibility layers and a small set of explicit executable entrypoints.

## Dependency direction

The intended dependency direction is:

```text
CLI / executable entrypoints
        ↓
orchestration / flow modules
        ↓
responsibility modules
        ↓
contracts + storage + process / provider integrations
```

Lower layers must not import orchestration surfaces back upward. Cross-cutting workflow policy is installed lazily at execution boundaries rather than by import-time mutation. Production modules are expected to stay at or below 700 lines; a module approaching that boundary should be split by responsibility rather than renamed into a generic `core` or `utils` bucket.

The retired standalone issue-runner and evaluation layers are not alternate production paths. Supported execution goes through the canonical AutoDev/OpenCode entrypoints or the maintained platform wrappers.

## Module map

### Canonical CLI and coordinator

- `autodev_cli.py` — canonical user CLI and command routing.
- `opencode_entrypoint.py`, `opencode_runtime.py` — OpenCode-facing execution routing.
- `role_coordinator_cli.py` — coordinator CLI contract.
- `role_coordinator_contract.py`, `role_coordinator_state.py` — coordinator state/contract.
- `role_coordinator_runtime.py`, `role_coordinator_stages.py`, `role_coordinator_flow.py` — runtime execution, stage transitions and coordinator flow.
- `coordination_contract.py`, `coordination_state.py` — shared runtime-neutral coordinator primitives.
- `role_runtime.py`, `opencode_role_runtime.py`, `role_resume.py` — runtime abstraction and durable role resume.

### Scheduling and autonomous queueing

- `scheduler.py` — scheduler command/orchestration surface.
- `scheduler_types.py` — scheduler state and contracts.
- `scheduler_process.py` — process/Git execution.
- `scheduler_backends.py` — native scheduler backends.
- `scheduler_registration.py` — install/uninstall lifecycle.
- `scheduler_health_*` — health state, probes, notification decisions, lifecycle and CLI.
- `queue_contract.py`, `queue_policy.py` — queue state and repository policy.
- `queue_github.py` — GitHub queue I/O.
- `queue_classification.py`, `queue_workflow.py`, `queue_presentation.py`, `queue_cli.py` — queue derivation, reconciliation, presentation and commands.
- `queue_selection.py` — deterministic runnable-issue selection without a facade import cycle.
- `claim_*` — distributed worker identity, Git-ref claim persistence, leases, recovery and CLI.

### Workflow stages

- `workflow_contract.py` — workflow constants, errors and shared contracts.
- `workflow_storage.py`, `workflow_commands.py`, `workflow_workspace.py` — persistence, subprocess/GitHub commands and workspace scope.
- `workflow_prompts.py`, `workflow_diagnostics.py` — prompt rendering and durable diagnostics.
- `workflow_github.py` — commit, PR and CI operations.
- `workflow_preparation.py`, `workflow_verification.py`, `workflow_dispatch.py` — preparation, verification and stage dispatch.
- `workflow_stages.py` — maintained integration surface; policy hooks are resolved lazily when execution begins.
- `windows_workflow_hooks.py` — lazily constructs Windows-aware workflow execution without import-time installation.

### Semantic verification and repair policy

- `semantic_contract.py`, `semantic_configuration.py`, `semantic_schema.py` — verifier contract, settings and schema parsing.
- `semantic_prompts.py`, `semantic_text.py`, `semantic_evidence.py` — prompts, bounded text and repository evidence.
- `semantic_storage.py`, `semantic_artifacts.py`, `semantic_invocation.py` — persistence, artifacts and model invocation.
- `semantic_cli.py` — executable semantic-verification CLI boundary used by platform wrappers.
- `repair_budget_contract.py`, `repair_budget_metrics.py`, `repair_budget_policy.py` — semantic-repair budget rules and sizing.
- `repair_budget_failure.py`, `repair_budget_storage.py`, `repair_budget_manifest.py`, `repair_budget_resume.py` — failure representation, persistence and resume integration.

Resume-budget semantics live in the repair-budget policy layer; workflow orchestration does not monkeypatch policy behavior.

### Model providers and privacy

- `provider_contract.py`, `provider_requests.py` — provider/model contracts and request shaping.
- `provider_command.py`, `provider_http.py`, `provider_headroom.py`, `provider_mock.py` — concrete transports.
- `provider_factory.py` — provider configuration and construction.
- `privacy_grant_contract.py`, `privacy_grant_store.py`, `privacy_grant_matching.py` — durable grant representation and matching.
- `privacy_grant_commands.py`, `privacy_grant_hooks.py`, `privacy_grant_cli.py` — consent commands and runtime integration.

### OpenCode integration and resume

- `opencode_adapter_contract.py`, `opencode_adapter_assets.py`, `opencode_adapter_models.py` — adapter contract, installed assets and role/model mapping.
- `opencode_adapter_storage.py`, `opencode_adapter_handoff.py`, `opencode_adapter_protocol.py` — durable state, handoffs and protocol checks.
- `opencode_adapter_roles.py`, `opencode_adapter_workflow.py`, `opencode_adapter_cli.py` — role preparation/acceptance, workflow integration and CLI.
- `opencode_resume_contract.py`, `opencode_resume_manifest.py`, `opencode_resume_checkpoint.py`, `opencode_resume_status.py`, `opencode_resume_execution.py` — durable resume ownership.

### Windows verification

- `windows_verification_contract.py`, `windows_verification_config.py` — verification contract and configuration.
- `windows_verification_storage.py`, `windows_verification_process.py`, `windows_verification_actions.py` — persistence, process execution and GitHub Actions access.
- `windows_verification_manifest.py`, `windows_verification_obligations.py`, `windows_verification_failure.py` — proof state, deferred obligations and failure classification.
- `windows_verification_execution.py`, `windows_verification_hooks.py` — execution and OpenCode integration.

There is no aggregate Windows-verification facade; callers depend on the owning responsibility module.

### Area Reader

- `area_reader/settings.py`, `area_reader/cli.py`, `area_reader/storage.py` — configuration, CLI parsing and persistence.
- `area_reader/repository.py`, `area_reader/routing.py`, `area_reader/context.py` — repository discovery, routing and context bundles.
- `area_reader/verification.py`, `area_reader/prompts.py`, `area_reader/provider.py` — recommended verification, prompts and provider calls.
- `area_reader/execution.py`, `area_reader/pipeline.py` — execution and pipeline orchestration.
- `area_reader/workflow.py` — supported standalone Area Reader entrypoint with provider/resume integration.

OpenCode context handoff imports the low-level Area Reader responsibility modules directly rather than routing through the standalone workflow.

## Architectural checks

`tests/test_python_architecture.py` permanently guards the boundaries introduced by issue #180. It checks that:

- production modules remain at or below the 700-line ceiling;
- the top-level local import graph is acyclic;
- representative responsibility modules import independently;
- removed compatibility/legacy module paths do not return;
- maintained docs do not point users at retired Python entrypoints; and
- temporary issue-180 migration workflows/scripts or chunk artifacts cannot accidentally ship.

Behavioral tests remain the authority for CLI, workflow, resume, privacy, provider, queue, scheduler and platform semantics. The architecture checks supplement those tests; they do not replace them.
'''
(ROOT / "docs" / "python-architecture.md").write_text(architecture_doc, encoding="utf-8")

architecture_test = '''from __future__ import annotations

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
    "automation.semantic_cli",
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
    "area_reader_v2",
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
'''
(ROOT / "tests" / "test_python_architecture.py").write_text(architecture_test, encoding="utf-8")

# This is the last issue-180 migration gate. Remove the gate itself before the
# permanent architecture test and final repository commit run.
(ROOT / ".github" / "workflows" / "issue-180-canonical-reachability.yml").unlink()
Path(__file__).unlink()
