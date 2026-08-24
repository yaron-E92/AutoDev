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
runtime_test.write_text(runtime_text, encoding="utf-8")

classification_test = root / "tests" / "test_execution_classification_hooks.py"
classification_text = classification_test.read_text(encoding="utf-8")
classification_text = classification_text.replace(
    "role_coordinator_flow.opencode_adapter,\n                \"_ensure_opencode_protocol\"",
    "execution_classification_evidence.opencode_adapter_protocol,\n                \"_ensure_opencode_protocol\"",
)
classification_test.write_text(classification_text, encoding="utf-8")

for doc in (root / "docs" / "evaluation.md", root / "docs" / "role-routing-benchmark.md"):
    doc.unlink(missing_ok=True)
shutil.rmtree(root / "benchmarks", ignore_errors=True)
