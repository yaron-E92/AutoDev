from pathlib import Path

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
path.write_text(text, encoding="utf-8")
