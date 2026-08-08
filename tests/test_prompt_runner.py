import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from automation import prompt_runner


def six_section_plan(path="docs/architecture.md", action="Update the doc."):
    return f"""1) Where to look
- {path}
2) Files / areas likely to touch
- {path}
3) Assumptions
- Documentation only.
4) Plan
- {action}
5) Risks / gotchas
- Keep scope narrow.
6) Recommended implementation approach
- Option A: edit {path}.
"""


class PromptRunnerTests(unittest.TestCase):
    def test_prompt_runner_direct_script_help_imports_sibling_modules(self):
        repo_root = Path(__file__).resolve().parents[1]
        completed = subprocess.run(
            [sys.executable, str(repo_root / "automation" / "prompt_runner.py"), "--help"],
            cwd=repo_root,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("Run one AutoDev prompt", completed.stderr + completed.stdout)

    def test_prompt_runner_module_help_still_works(self):
        repo_root = Path(__file__).resolve().parents[1]
        completed = subprocess.run(
            [sys.executable, "-m", "automation.prompt_runner", "--help"],
            cwd=repo_root,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("Run one AutoDev prompt", completed.stderr + completed.stdout)

    def test_ollama_provider_runs_model_with_prompt_on_stdin(self):
        calls = []
        original_run = prompt_runner.subprocess.run

        def fake_run(argv, **kwargs):
            calls.append((argv, kwargs))
            return subprocess.CompletedProcess(argv, 0, "planner output", "")

        try:
            prompt_runner.subprocess.run = fake_run
            output = prompt_runner.run_ollama_provider("qwen35-9b-32k", "prompt text")
        finally:
            prompt_runner.subprocess.run = original_run

        self.assertEqual(output, "planner output")
        self.assertEqual(calls[0][0], ["ollama", "run", "qwen35-9b-32k"])
        self.assertEqual(calls[0][1]["input"], "prompt text")

    def test_command_provider_appends_prompt_when_no_placeholder(self):
        calls = []
        original_run = prompt_runner.subprocess.run

        def fake_run(argv, **kwargs):
            calls.append((argv, kwargs))
            return subprocess.CompletedProcess(argv, 0, "BEGIN_UNIFIED_DIFF\npatch\nEND_UNIFIED_DIFF\n", "")

        try:
            prompt_runner.subprocess.run = fake_run
            output = prompt_runner.run_command_provider("model-cli --flag", "prompt text")
        finally:
            prompt_runner.subprocess.run = original_run

        self.assertIn("BEGIN_UNIFIED_DIFF", output)
        self.assertEqual(calls[0][0], ["model-cli", "--flag", "prompt text"])

    def test_command_provider_substitutes_prompt_file_placeholder(self):
        calls = []
        original_run = prompt_runner.subprocess.run

        def fake_run(command, **kwargs):
            calls.append((command, kwargs))
            return subprocess.CompletedProcess(command, 0, "# Plan", "")

        try:
            prompt_runner.subprocess.run = fake_run
            output = prompt_runner.run_command_provider("model-cli {prompt_file}", "prompt text", Path("prompt.md"))
        finally:
            prompt_runner.subprocess.run = original_run

        self.assertEqual(output, "# Plan")
        self.assertIn("prompt.md", calls[0][0])
        self.assertTrue(calls[0][1]["shell"])

    def test_planner_stdout_is_written_to_plan_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            plan_path = Path(temp_dir) / ".autodev-run" / "current" / "plan.md"

            plan = six_section_plan()
            prompt_runner.handle_planner_output(plan, plan_path)

            self.assertEqual(plan_path.read_text(encoding="utf-8"), plan)

    def test_planner_output_strips_thinking_preamble_when_final_plan_is_clear(self):
        output = """Thinking...
private scratch

1) Where to look
- src/App.xaml.cs
2) Files / areas likely to touch
- src/App.xaml.cs
3) Assumptions
- Small local fix.
4) Plan
- Inspect and patch the file.
5) Risks / gotchas
- Keep scope narrow.
6) Recommended implementation approach
- Option A: patch the file.
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            plan_path = Path(temp_dir) / "plan.md"

            prompt_runner.handle_planner_output(output, plan_path)

            self.assertTrue(plan_path.read_text(encoding="utf-8").startswith("1) Where to look\n- src/App.xaml.cs\n"))
            self.assertEqual(plan_path.with_name("plan.md.raw").read_text(encoding="utf-8"), output)

    def test_planner_output_fails_when_preamble_cannot_be_safely_stripped(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            plan_path = Path(temp_dir) / "plan.md"

            with self.assertRaises(prompt_runner.PromptRunnerError) as raised:
                prompt_runner.handle_planner_output("Thinking...\nMaybe inspect files", plan_path)

            self.assertIn("raw response", str(raised.exception))
            self.assertTrue(plan_path.with_name("plan.md.raw").is_file())

    def test_planner_output_strips_control_chars_and_extracts_required_sections(self):
        output = "\x1b[?25lThinking...\b\b draft\r\n" """1) Where to look
- docs/architecture.md
2) Files / areas likely to touch
- docs/architecture.md
3) Assumptions
- Documentation only.
4) Plan
- Update the doc.
5) Risks / gotchas
- Keep scope narrow.
6) Recommended implementation approach
- Option A: edit docs only.
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            plan_path = Path(temp_dir) / "plan.md"

            prompt_runner.handle_planner_output(output, plan_path)

            plan = plan_path.read_text(encoding="utf-8")
            self.assertTrue(plan.startswith("1) Where to look"))
            self.assertNotIn("Thinking", plan)
            self.assertNotRegex(plan, r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

    def test_planner_output_extracts_last_complete_six_section_plan(self):
        output = (
            "Reasoning about an earlier draft.\n"
            + six_section_plan("docs/wrong.md", "Do the broad draft.")
            + "\nMore notes before the final answer.\n"
            + six_section_plan("docs/final.md", "Apply the narrowed change.")
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            plan_path = Path(temp_dir) / "plan.md"

            prompt_runner.handle_planner_output(output, plan_path)

            plan = plan_path.read_text(encoding="utf-8")
            self.assertIn("docs/final.md", plan)
            self.assertNotIn("docs/wrong.md", plan)

    def test_planner_output_rejects_contaminated_final_plan(self):
        output = six_section_plan("docs/architecture.md", "Wait, let's refine this before editing.")
        with tempfile.TemporaryDirectory() as temp_dir:
            plan_path = Path(temp_dir) / "plan.md"

            with self.assertRaises(prompt_runner.PromptRunnerError):
                prompt_runner.handle_planner_output(output, plan_path)

            self.assertTrue(plan_path.with_name("plan.md.raw").is_file())
            self.assertTrue(plan_path.with_name("plan.md.parser-error.md").is_file())
            self.assertFalse(plan_path.exists())

    def test_planner_output_rejects_reasoning_after_extracted_plan(self):
        output = """Intro
1) Where to look
- docs/architecture.md
2) Files / areas likely to touch
- docs/architecture.md
3) Assumptions
- Documentation only.
4) Plan
- Update the doc.
5) Risks / gotchas
- Keep scope narrow.
6) Recommended implementation approach
- Option A: edit docs only.
Thinking... hidden scratchpad
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            plan_path = Path(temp_dir) / "plan.md"

            with self.assertRaises(prompt_runner.PromptRunnerError):
                prompt_runner.handle_planner_output(output, plan_path)

            self.assertTrue(plan_path.with_name("plan.md.raw").is_file())
            self.assertTrue(plan_path.with_name("plan.md.parser-error.md").is_file())
            self.assertFalse(plan_path.exists())
    def test_verifier_stdout_requires_pass_or_fail_first_line(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            result_path = Path(temp_dir) / "verification-result.md"

            prompt_runner.handle_verifier_output("PASS\nLooks good\n", result_path)

            self.assertEqual(result_path.read_text(encoding="utf-8"), "PASS\nLooks good\n")
            with self.assertRaises(prompt_runner.PromptRunnerError):
                prompt_runner.handle_verifier_output("MAYBE\n", result_path)

    def test_implementer_output_applies_diff_and_writes_commit_message(self):
        output = """COMMIT_MESSAGE: Update AutoDev workflow
BEGIN_UNIFIED_DIFF
diff --git a/file.txt b/file.txt
--- a/file.txt
+++ b/file.txt
@@ -1 +1 @@
-old
+new
END_UNIFIED_DIFF
"""
        applied = []
        with tempfile.TemporaryDirectory() as temp_dir:
            commit_path = Path(temp_dir) / "commit-message.txt"

            changed = prompt_runner.handle_patch_output(
                output,
                role="implementer",
                commit_message_file=commit_path,
                apply_patch_fn=applied.append,
            )

            self.assertTrue(changed)
            self.assertIn("diff --git a/file.txt b/file.txt", applied[0])
            self.assertEqual(commit_path.read_text(encoding="utf-8"), "Update AutoDev workflow")

    def test_no_changes_required_skips_patch_application(self):
        applied = []

        changed = prompt_runner.handle_patch_output(
            "NO_CHANGES_REQUIRED\nAlready done",
            role="repair",
            apply_patch_fn=applied.append,
        )

        self.assertFalse(changed)
        self.assertEqual(applied, [])


if __name__ == "__main__":
    unittest.main()
