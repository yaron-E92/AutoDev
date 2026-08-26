from __future__ import annotations

import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from automation import autodev_cli


ROOT = Path(__file__).resolve().parents[1]


class ManageHelpAndDocsTests(unittest.TestCase):
    @staticmethod
    def render(args: list[str]) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = autodev_cli.run(args)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_manage_is_discoverable_and_help_explains_authorization_boundary(self) -> None:
        top = self.render(["--help"])
        manage = self.render(["manage", "--help"])
        help_alias = self.render(["help", "manage"])

        self.assertEqual(top[0], 0)
        self.assertIn("manage", top[1])
        self.assertEqual(manage, help_alias)
        self.assertEqual(manage[0], 0)
        self.assertEqual(manage[2], "")
        self.assertIn("autodev manage (ISSUE | --all | --list)", manage[1])
        self.assertIn("--all", manage[1])
        self.assertIn("--list", manage[1])
        self.assertIn("--json", manage[1])
        self.assertIn("operator authorization, not readiness", manage[1])
        self.assertIn("never adds `autodev:ready`", manage[1])
        self.assertIn("strictly read-only", manage[1])
        self.assertIn("autodev manage '#123'", manage[1])
        self.assertNotIn("explicit consent grant", manage[1])

    def test_manage_documentation_preserves_managed_vs_ready_semantics(self) -> None:
        text = (ROOT / "docs" / "manage.md").read_text(encoding="utf-8")

        self.assertIn("autodev manage 123", text)
        self.assertIn("autodev manage --all", text)
        self.assertIn("autodev manage --list", text)
        self.assertIn("autodev manage --list --json", text)
        self.assertIn("`autodev:managed`", text)
        self.assertIn("different from `autodev:ready`", text)
        self.assertIn("does **not**", text)
        self.assertIn("start an issue-to-PR run", text)
        self.assertIn("Pull requests are excluded", text)
        self.assertIn("strictly read-only", text)
        self.assertIn("autodev repo ensure-labels", text)


if __name__ == "__main__":
    unittest.main()
