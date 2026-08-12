import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
OPEN_CODE_ROOT = REPO_ROOT / "integrations" / "opencode"


class OpenCodeResumeGuardrailTests(unittest.TestCase):
    def test_resume_command_uses_installer_launcher_without_shell_fallbacks(self):
        text = (OPEN_CODE_ROOT / "commands" / "autodev-resume.md").read_text(encoding="utf-8")
        self.assertIn("use its non-empty `python` field as the exact launcher", text)
        self.assertIn("Do not probe `python`, `python3`", text)
        self.assertIn("python .opencode/autodev.py resume", text)
        self.assertIn("do not try alternate shell commands", text)
        self.assertNotIn("use `python3` instead where required", text)

    def test_resume_command_makes_bridge_boundary_authoritative(self):
        text = (OPEN_CODE_ROOT / "commands" / "autodev-resume.md").read_text(encoding="utf-8")
        self.assertIn("sole authority for the continuation boundary", text)
        self.assertIn("Never infer a completed role", text)
        self.assertIn("invent a Task ID", text)
        self.assertIn("do not continue manually", text)

    def test_resume_command_revalidates_durable_progress_after_role_tasks(self):
        text = (OPEN_CODE_ROOT / "commands" / "autodev-resume.md").read_text(encoding="utf-8")
        self.assertIn("After every delegated role Task", text)
        self.assertIn("do not trust the Task UI checkmark", text)
        self.assertIn("Run the exact resume bridge again", text)
        self.assertIn("missing/unaccepted durable progress", text)

    def test_resume_command_requires_explicit_terminal_state(self):
        text = (OPEN_CODE_ROOT / "commands" / "autodev-resume.md").read_text(encoding="utf-8")
        self.assertIn("exactly one explicit final state", text)
        self.assertIn("`PR_READY`, `BLOCKED`, or `FAILED`", text)
        self.assertIn("Never merge the pull request", text)


if __name__ == "__main__":
    unittest.main()
