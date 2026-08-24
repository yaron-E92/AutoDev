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
old = '''            with patch(
                "automation.workflow_prompts.gh",
                return_value=SimpleNamespace(
                    returncode=0,
                    stdout="diff --git a/file b/file\\n",
                    stderr="",
                ),
            ):
                workflow_prompts.render_legacy_verifier(
                    repo,
                    current,
                    state,
                    REPO_ROOT,
                )
'''
new = '''            workflow_prompts.render_legacy_verifier(
                repo,
                current,
                state,
                REPO_ROOT,
                runner=lambda *args, **kwargs: SimpleNamespace(
                    returncode=0,
                    stdout="diff --git a/file b/file\\n",
                    stderr="",
                ),
            )
'''
if old in text:
    text = text.replace(old, new)
elif new not in text:
    raise SystemExit("legacy verifier test block not found")
path.write_text(text, encoding="utf-8")
