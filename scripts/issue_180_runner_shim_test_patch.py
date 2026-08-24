from pathlib import Path

root = Path(__file__).resolve().parents[1]
path = root / "tests" / "test_workflow_stages.py"
text = path.read_text(encoding="utf-8")
text = text.replace(
    "from automation import workflow_stages\n",
    "from automation import workflow_prompts, workflow_stages\n",
)
text = text.replace(
    '"automation.workflow_stages_core.gh"',
    '"automation.workflow_prompts.gh"',
)
text = text.replace(
    "workflow_stages.render_legacy_verifier(\n",
    "workflow_prompts.render_legacy_verifier(\n",
)
path.write_text(text, encoding="utf-8")
