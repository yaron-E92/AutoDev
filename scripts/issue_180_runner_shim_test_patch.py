from pathlib import Path

root = Path(__file__).resolve().parents[1]
path = root / "tests" / "test_workflow_stages.py"
text = path.read_text(encoding="utf-8")
text = text.replace(
    '"automation.workflow_stages_core.gh"',
    '"automation.workflow_prompts.gh"',
)
path.write_text(text, encoding="utf-8")
