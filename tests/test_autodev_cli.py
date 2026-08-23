from __future__ import annotations

import io
import os
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from automation import autodev_cli


class AutoDevCliTests(unittest.TestCase):
    def test_help_prefers_first_class_autodev_commands(self):
        output = io.StringIO()
        with redirect_stdout(output):
            code = autodev_cli.run(["--help"])

        self.assertEqual(code, 0)
        text = output.getvalue()
        self.assertIn("autodev install --user", text)
        self.assertIn("autodev repo doctor", text)
        self.assertIn("autodev scheduler install", text)
        self.assertIn("autodev scheduler run-once", text)
        self.assertIn("autodev coordinate", text)
        self.assertNotIn("python3 .opencode/autodev.py", text)

    def test_install_routes_to_user_installer(self):
        with patch.object(autodev_cli.user_install, "run_cli", return_value=7) as run_cli:
            code = autodev_cli.run(["install", "--user", "--json"])

        self.assertEqual(code, 7)
        run_cli.assert_called_once()
        self.assertEqual(run_cli.call_args.args[0], ["--user", "--json"])

    def test_repo_routes_to_repo_setup(self):
        with patch("automation.repo_setup.run_cli", return_value=5) as run_cli:
            code = autodev_cli.run(["repo", "doctor", "--json"])

        self.assertEqual(code, 5)
        run_cli.assert_called_once_with(["doctor", "--json"])

    def test_scheduler_routes_to_scheduler_core(self):
        with patch("automation.scheduler.run_cli", return_value=6) as run_cli:
            code = autodev_cli.run(["scheduler", "status", "--json"])

        self.assertEqual(code, 6)
        run_cli.assert_called_once_with(["status", "--json"])

    def test_resume_maps_to_shared_python_coordinator(self):
        with patch.object(autodev_cli, "_enable_interactive_consent_for_direct_cli"), patch.object(
            autodev_cli.opencode_entrypoint,
            "run",
            return_value=0,
        ) as run_entrypoint:
            code = autodev_cli.run(["resume", "--invalidate-role", "planner"])

        self.assertEqual(code, 0)
        run_entrypoint.assert_called_once_with(
            ["coordinate", "--resume", "--invalidate-role", "planner"]
        )

    def test_existing_commands_share_opencode_entrypoint_core(self):
        with patch.object(autodev_cli, "_enable_interactive_consent_for_direct_cli"), patch.object(
            autodev_cli.opencode_entrypoint,
            "run",
            return_value=0,
        ) as run_entrypoint:
            for command in (
                ["status"],
                ["coordinate", "--arguments", "123"],
                ["privacy", "list"],
                ["queue", "status"],
            ):
                with self.subTest(command=command):
                    self.assertEqual(autodev_cli.run(command), 0)

        self.assertEqual(
            [call.args[0] for call in run_entrypoint.call_args_list],
            [
                ["status"],
                ["coordinate", "--arguments", "123"],
                ["privacy", "list"],
                ["queue", "status"],
            ],
        )

    def test_internal_interactive_marker_is_consumed_before_shared_parser(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(
            autodev_cli.opencode_entrypoint,
            "run",
            return_value=0,
        ) as run_entrypoint:
            code = autodev_cli.run(
                [
                    "coordinate",
                    "--interactive-consent",
                    "--arguments",
                    "123",
                ]
            )
            consent = os.environ.get(autodev_cli.INTERACTIVE_CONSENT_ENV)

        self.assertEqual(code, 0)
        self.assertEqual(consent, autodev_cli.INTERACTIVE_CONSENT_VALUE)
        run_entrypoint.assert_called_once_with(["coordinate", "--arguments", "123"])


if __name__ == "__main__":
    unittest.main()
