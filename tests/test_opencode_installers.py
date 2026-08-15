from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from automation import opencode_adapter, opencode_install


REPO_ROOT = Path(__file__).resolve().parents[1]


class OpenCodeInstallerTests(unittest.TestCase):
    def test_canonical_installer_copies_stable_windows_workflow_without_autodev_sha(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir)
            installed = opencode_install.install_assets(
                target,
                REPO_ROOT,
                python_command="python3",
            )
            workflow = target / opencode_install.WINDOWS_CALLER_TARGET
            first = workflow.read_text(encoding="utf-8")
            opencode_install.install_assets(
                target,
                REPO_ROOT,
                python_command="python3",
            )
            second = workflow.read_text(encoding="utf-8")

        self.assertIn(workflow, installed)
        self.assertEqual(first, second)
        self.assertIn("autodev_ref:", first)
        self.assertIn("ref: ${{ inputs.autodev_ref }}", first)
        self.assertNotIn("__AUTODEV_WORKFLOW_REF__", first)

    def test_deprecated_adapter_install_delegates_to_complete_canonical_installer(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir)
            stderr = io.StringIO()
            stdout = io.StringIO()
            with redirect_stderr(stderr), redirect_stdout(stdout):
                code = opencode_adapter.run(
                    [
                        "install",
                        "--target-repo",
                        str(target),
                        "--autodev-root",
                        str(REPO_ROOT),
                        "--python",
                        "python3",
                    ]
                )
            workflow = target / opencode_install.WINDOWS_CALLER_TARGET
            workflow_exists = workflow.is_file()

        self.assertEqual(code, 0)
        self.assertTrue(workflow_exists)
        self.assertIn("DEPRECATED", stderr.getvalue())
        self.assertIn("automation.opencode_install", stderr.getvalue())
        self.assertIn("Installed", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
