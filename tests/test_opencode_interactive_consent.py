from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from automation import opencode_install

REPO_ROOT = Path(__file__).resolve().parents[1]
INTERACTIVE_COMMANDS = (
    "autodev-issue-to-pr.md",
    "autodev-resume.md",
    "autodev-read.md",
    "autodev-plan.md",
    "autodev-implement.md",
    "autodev-fix.md",
    "autodev-verify.md",
)


class OpenCodeInteractiveConsentTests(unittest.TestCase):
    def test_installed_commands_use_first_class_cli_and_opt_into_interactive_consent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir).resolve()
            opencode_install.install_assets(target, REPO_ROOT)
            rendered = {
                name: (target / ".opencode" / "commands" / name).read_text(encoding="utf-8")
                for name in INTERACTIVE_COMMANDS
            }

        for name, content in rendered.items():
            with self.subTest(command=name):
                self.assertIn("--interactive-consent", content)
                self.assertIn("!`autodev ", content)
                self.assertNotIn(".opencode/autodev", content)
                self.assertNotIn("__AUTODEV_PYTHON_SHELL__", content)


if __name__ == "__main__":
    unittest.main()
