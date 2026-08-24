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

# The migration scripts and historical docs necessarily contain the old names while
# performing the deletion. Production code must not retain them; the final architecture
# pass cleans documentation references after source deletion is proven.
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
integration.write_text(integration_text, encoding="utf-8")

for doc in (root / "docs" / "evaluation.md", root / "docs" / "role-routing-benchmark.md"):
    doc.unlink(missing_ok=True)
shutil.rmtree(root / "benchmarks", ignore_errors=True)
