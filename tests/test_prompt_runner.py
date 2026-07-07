import subprocess
import tempfile
import unittest
from pathlib import Path

from automation import prompt_runner


class PromptRunnerTests(unittest.TestCase):
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
            plan_path = Path(temp_dir) / ".codex-run" / "current" / "plan.md"

            prompt_runner.handle_planner_output("# Plan\n", plan_path)

            self.assertEqual(plan_path.read_text(encoding="utf-8"), "# Plan\n")

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
