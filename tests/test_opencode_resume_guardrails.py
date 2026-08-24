import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
OPEN_CODE_ROOT = REPO_ROOT / "integrations" / "opencode"


class OpenCodeResumeGuardrailTests(unittest.TestCase):
    def test_resume_command_is_display_only_frontend_for_canonical_cli(self):
        text = (OPEN_CODE_ROOT / "commands" / "autodev-resume.md").read_text(encoding="utf-8")
        self.assertIn('autodev coordinate --resume', text)
        self.assertIn('--interactive-consent', text)
        self.assertIn('display-only', text)
        self.assertIn('owned entirely by Python', text)
        self.assertNotIn('.opencode/autodev.py', text)
        self.assertNotIn('.opencode/autodev.json', text)
    def test_reader_consumes_python_prepared_bundle_without_repository_discovery_tools(self):
        text = (OPEN_CODE_ROOT / "agents" / "autodev-reader.md").read_text(encoding="utf-8")
        self.assertIn('permission:\n  "*": deny', text)
        self.assertIn("glob: deny", text)
        self.assertIn("grep: deny", text)
        self.assertIn("list: deny", text)
        self.assertIn('read:\n    "*": deny', text)
        self.assertIn('".autodev-run/current/reader.md": allow', text)
        self.assertIn("Python bridge owns repository discovery", text)
        self.assertIn("Never construct an absolute repository path", text)


if __name__ == "__main__":
    unittest.main()
