import ast
import shutil
from pathlib import Path

root = Path(__file__).resolve().parents[1]
path = Path(__file__).resolve().with_name("issue_180_remove_legacy_runner.py")
text = path.read_text(encoding="utf-8")
anchor = '    "workflow_stage": "opencode_adapter_workflow",\n'
addition = '''    **{name: "semantic_evidence" for name in (
        "collect_changed_files", "collect_cross_file_regression_evidence",
        "collect_current_diff", "collect_deterministic_evidence",
    )},
    **{name: "semantic_prompts" for name in (
        "build_schema_repair_prompt", "build_semantic_prompt", "extract_acceptance_criteria",
    )},
    **{name: "semantic_schema" for name in ("parse_semantic_output", "semantic_result_template")},
    "render_template": "semantic_text",
    **{name: "semantic_artifacts" for name in ("write_final_verdict", "write_semantic_result")},
    "SemanticVerifierError": "semantic_contract",
    "sanitize_model_output": "model_output_sanitizer",
    **{name: "model_providers" for name in ("ProviderError", "load_provider_config")},
    **{name: "prompt_runner" for name in ("REQUIRED_PLAN_HEADINGS", "PromptRunnerError", "handle_planner_output")},
'''
if addition not in text:
    if anchor not in text:
        raise SystemExit("adapter owner mapping anchor missing")
    text = text.replace(anchor, addition + anchor)

production_anchor = '    "issue_run_session.py",\n]'
production_addition = '''    "issue_run_session.py",
    "eval_harness.py",
    "eval_harness_core.py",
    "eval_worktree.py",
    "evaluation_cli.py",
    "evaluation_contract.py",
    "evaluation_execution.py",
    "evaluation_profiles.py",
    "evaluation_reporting.py",
    "evaluation_scoring.py",
    "role_routing_benchmark.py",
]'''
if production_anchor in text:
    text = text.replace(production_anchor, production_addition)

test_anchor = 'LEGACY_TESTS = ["test_run_real_issue.py", "test_run_resume.py"]'
test_replacement = '''LEGACY_TESTS = [
    "test_run_real_issue.py",
    "test_run_resume.py",
    "test_eval_harness.py",
    "test_eval_worktree.py",
    "test_role_routing_benchmark.py",
]'''
text = text.replace(test_anchor, test_replacement)
text = text.replace(
    'for parent in (AUTOMATION, TESTS, ROOT / "docs", ROOT / "scripts"):',
    'for parent in (AUTOMATION,):',
)
path.write_text(text, encoding="utf-8")


def remove_methods(target: Path, names: set[str]) -> None:
    source = target.read_text(encoding="utf-8")
    tree = ast.parse(source)
    ranges: list[tuple[int, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
            ranges.append((node.lineno, node.end_lineno or node.lineno))
    missing = names - {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    if missing:
        raise SystemExit(f"methods not found in {target.relative_to(root)}: {sorted(missing)}")
    lines = source.splitlines(keepends=True)
    for start, end in sorted(ranges, reverse=True):
        del lines[start - 1 : end]
    target.write_text("".join(lines), encoding="utf-8")


integration = root / "tests" / "test_opencode_integration.py"
integration_text = integration.read_text(encoding="utf-8")
integration_text = integration_text.replace(
    'REPO_ROOT / "automation" / "opencode_adapter.py"',
    'REPO_ROOT / "automation" / "opencode_adapter_workflow.py"',
)
integration_text = integration_text.replace(
    "def test_opencode_adapter_has_no_windows_workflow_backend(self):",
    "def test_opencode_workflow_adapter_has_no_windows_workflow_backend(self):",
)
integration_text = integration_text.replace(
    '"automation.opencode_adapter.workflow_stages.execute_stage"',
    '"automation.opencode_adapter_workflow.workflow_stages.execute_stage"',
)
integration_text = integration_text.replace(
    '"automation.opencode_adapter.workflow_stages.ensure_prepared_issue"',
    '"automation.opencode_adapter_protocol.workflow_stages.ensure_prepared_issue"',
)
integration_text = integration_text.replace(
    '"automation.opencode_adapter.resolve_opencode_model_mappings"',
    '"automation.opencode_adapter_cli.resolve_opencode_model_mappings"',
)
integration_text = integration_text.replace(
    '"automation.opencode_adapter.collect_changed_files"',
    '"automation.opencode_adapter_roles.collect_changed_files"',
).replace(
    '"automation.opencode_adapter.collect_current_diff"',
    '"automation.opencode_adapter_roles.collect_current_diff"',
).replace(
    '"automation.opencode_adapter.collect_deterministic_evidence"',
    '"automation.opencode_adapter_roles.collect_deterministic_evidence"',
)
start = integration_text.find("    def test_existing_workflow_entrypoints_do_not_depend_on_opencode_adapter(self):\n")
if start >= 0:
    end = integration_text.find("    def _write_state", start)
    if end < 0:
        raise SystemExit("cannot remove obsolete workflow-entrypoint compatibility test")
    integration_text = integration_text[:start] + integration_text[end:]
integration.write_text(integration_text, encoding="utf-8")

privacy_test = root / "tests" / "test_opencode_privacy_role_entrypoint.py"
privacy_text = privacy_test.read_text(encoding="utf-8")
privacy_text = privacy_text.replace(
    'patch.object(\n            opencode_adapter,\n            "prepare_role",',
    'patch.object(\n            opencode_role_entrypoint.opencode_adapter_roles,\n            "prepare_role",',
)
privacy_test.write_text(privacy_text, encoding="utf-8")

runtime_test = root / "tests" / "test_role_runtime.py"
runtime_text = runtime_test.read_text(encoding="utf-8")
runtime_text = runtime_text.replace(
    'patch.object(\n            opencode_adapter,\n            "resolve_opencode_model_mappings",',
    'patch.object(\n            opencode_role_runtime.opencode_adapter_models,\n            "resolve_opencode_model_mappings",',
)
user_config_test = '''    def test_user_config_is_below_repository_config_and_above_default(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repo = root / "repo"
            repo.mkdir()
            user_config = root / "user-config.json"
            user_config.write_text(
                json.dumps({"role_runtime": "user-runtime"}),
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {role_runtime.USER_CONFIG_ENV: str(user_config)},
                clear=True,
            ):
                self.assertEqual(role_runtime.resolve_runtime_name(repo)[0], "user-runtime")
                repo_config = repo / ".autodev" / "config.json"
                repo_config.parent.mkdir(parents=True)
                repo_config.write_text(
                    json.dumps({"role_runtime": "repo-runtime"}),
                    encoding="utf-8",
                )
                self.assertEqual(role_runtime.resolve_runtime_name(repo)[0], "repo-runtime")

'''
if "test_user_config_is_below_repository_config_and_above_default" not in runtime_text:
    marker = "\n\nclass OpenCodeRoleRuntimeTests(unittest.TestCase):"
    if marker not in runtime_text:
        raise SystemExit("role runtime class marker missing")
    runtime_text = runtime_text.replace(marker, "\n\n" + user_config_test + marker, 1)
runtime_test.write_text(runtime_text, encoding="utf-8")

classification_test = root / "tests" / "test_execution_classification_hooks.py"
classification_text = classification_test.read_text(encoding="utf-8")
classification_text = classification_text.replace(
    "role_coordinator_flow.opencode_adapter,\n                \"_ensure_opencode_protocol\"",
    "execution_classification_evidence.opencode_adapter_protocol,\n                \"_ensure_opencode_protocol\"",
)
classification_test.write_text(classification_text, encoding="utf-8")

headroom_test = root / "tests" / "test_headroom.py"
remove_methods(
    headroom_test,
    {
        "test_implementer_compression_preserves_issue_constraints_and_output_contract",
        "test_compression_failure_fails_open_to_original_prompt",
        "test_compression_telemetry_stays_out_of_model_text_and_debug_artifact_is_safe",
    },
)
headroom_text = headroom_test.read_text(encoding="utf-8")
headroom_text = headroom_text.replace("from automation import run_real_issue\n", "")
headroom_text = headroom_text.replace("from automation.run_real_issue_core import build_implementation_prompt\n", "")
headroom_test.write_text(headroom_text, encoding="utf-8")

model_roles_test = root / "tests" / "test_model_roles.py"
remove_methods(model_roles_test, {"test_dry_run_routes_initial_patch_to_implementer_only"})
model_roles_text = model_roles_test.read_text(encoding="utf-8")
model_roles_text = model_roles_text.replace("import io\n", "")
model_roles_text = model_roles_text.replace("from automation import run_real_issue\n", "")
model_roles_test.write_text(model_roles_text, encoding="utf-8")

semantic_test = root / "tests" / "test_semantic_verifier.py"
remove_methods(
    semantic_test,
    {
        "setUp",
        "tearDown",
        "test_operational_gate_uses_independent_verifier_and_writes_final_verdict",
        "test_operational_repair_uses_fixer_then_reruns_deterministic_and_semantic_checks",
        "test_provider_failure_is_not_reported_as_semantic_blocked",
    },
)
semantic_text = semantic_test.read_text(encoding="utf-8")
semantic_text = semantic_text.replace("from automation import prompt_runner, run_real_issue\n", "from automation import prompt_runner\n")
semantic_text = semantic_text.replace("from automation.run_manifest import create_manifest\n", "")
semantic_test.write_text(semantic_text, encoding="utf-8")
(root / "tests" / "test_semantic_verifier_failures.py").unlink(missing_ok=True)

solution_test = root / "tests" / "test_solution_filter_preference.py"
solution_text = solution_test.read_text(encoding="utf-8")
old_import = '''from area_reader.workflow import (
    PREFERRED_SOLUTION_FILTER_MARKERS,
    build_verification_command_groups,
    collect_repo_files,
    detect_repo_facts,
    preferred_solution_filter,
)
'''
new_import = '''from area_reader.settings import PREFERRED_SOLUTION_FILTER_MARKERS
from area_reader.repository import collect_repo_files, detect_repo_facts
from area_reader.verification import build_verification_command_groups, preferred_solution_filter
'''
if old_import not in solution_text:
    raise SystemExit("solution filter facade import not found")
solution_test.write_text(solution_text.replace(old_import, new_import), encoding="utf-8")

architecture_test = root / "tests" / "test_python_architecture.py"
architecture_text = architecture_test.read_text(encoding="utf-8")
architecture_text = architecture_text.replace('    "automation.issue_run_entrypoint",\n', "")
architecture_text = architecture_text.replace('    "automation.evaluation_reporting",\n', "")
architecture_text = architecture_text.replace('    "automation.role_coord_flow",\n', '    "automation.role_coordinator_flow",\n')
architecture_text = architecture_text.replace('    "automation.opencode_coord_flow",\n', "")
architecture_text = architecture_text.replace(
    'COMPATIBILITY_SHIMS = (\n    ROOT / "automation" / "workflow_stages_core.py",\n    ROOT / "automation" / "run_real_issue_core.py",\n)\n',
    "",
)
architecture_test.write_text(architecture_text, encoding="utf-8")
remove_methods(architecture_test, {"test_legacy_core_files_are_compatibility_shims_not_dumping_grounds"})
architecture_text = architecture_test.read_text(encoding="utf-8")
architecture_text = architecture_text.replace(
    '            for path in parent.glob("issue-180-*")\n',
    '            for path in parent.glob("issue-180-*")\n            if path.name not in {"issue-180-canonical-reachability.yml", "issue-180-remove-legacy-runner.yml"}\n',
)
architecture_test.write_text(architecture_text, encoding="utf-8")

(root / "tests" / "test_role_runtime_compat.py").unlink(missing_ok=True)

for doc in (root / "docs" / "evaluation.md", root / "docs" / "role-routing-benchmark.md"):
    doc.unlink(missing_ok=True)
shutil.rmtree(root / "benchmarks", ignore_errors=True)
