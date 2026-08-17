from __future__ import annotations

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from automation import opencode_install


REPO_ROOT = Path(__file__).resolve().parents[1]
BRIDGE_PATH = REPO_ROOT / "integrations" / "opencode" / "autodev.py"
SPEC = importlib.util.spec_from_file_location("autodev_opencode_interactive_bridge", BRIDGE_PATH)
assert SPEC is not None and SPEC.loader is not None
BRIDGE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BRIDGE)


class OpenCodeInteractiveConsentTests(unittest.TestCase):
    def test_bridge_consumes_internal_interactive_consent_argument(self):
        arguments, interactive = BRIDGE._consume_interactive_consent_argument(
            ["coordinate", "--resume", "--interactive-consent", "--arguments", "32"]
        )

        self.assertTrue(interactive)
        self.assertEqual(
            arguments,
            ["coordinate", "--resume", "--arguments", "32"],
        )

    def test_bridge_does_not_mark_normal_invocation_interactive(self):
        arguments, interactive = BRIDGE._consume_interactive_consent_argument(
            ["coordinate", "--resume"]
        )

        self.assertFalse(interactive)
        self.assertEqual(arguments, ["coordinate", "--resume"])

    def test_bridge_environment_marks_only_explicit_interactive_consent(self):
        with patch.dict(os.environ, {}, clear=True):
            interactive = BRIDGE._bridge_environment(
                "python3",
                REPO_ROOT,
                REPO_ROOT,
                interactive_consent=True,
            )
            unattended = BRIDGE._bridge_environment(
                "python3",
                REPO_ROOT,
                REPO_ROOT,
                interactive_consent=False,
            )

        self.assertEqual(
            interactive[BRIDGE.INTERACTIVE_CONSENT_ENV],
            BRIDGE.INTERACTIVE_CONSENT_VALUE,
        )
        self.assertNotIn(BRIDGE.INTERACTIVE_CONSENT_ENV, unattended)

    def test_installed_python_commands_opt_into_interactive_consent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir).resolve()
            opencode_install.install_assets(
                target,
                REPO_ROOT,
                python_command="python3",
            )
            rendered = {
                name: (target / ".opencode" / "commands" / name).read_text(encoding="utf-8")
                for name in opencode_install.PYTHON_COMMAND_TEMPLATES
            }

        for name, content in rendered.items():
            with self.subTest(command=name):
                self.assertIn("--interactive-consent", content)
                self.assertNotIn(opencode_install.PYTHON_SHELL_PLACEHOLDER, content)


if __name__ == "__main__":
    unittest.main()
