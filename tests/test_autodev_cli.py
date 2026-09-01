from __future__ import annotations

from automation import claim_cli, scheduler_health_cli

import io
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from automation import autodev_cli


class AutoDevCliTests(unittest.TestCase):
    def test_help_prefers_first_class_autodev_commands(self):
        output = io.StringIO()
        with redirect_stdout(output):
            code = autodev_cli.run(["--help"])

        self.assertEqual(code, 0)
        text = output.getvalue()
        self.assertIn("AutoDev autonomously turns GitHub issues into reviewed pull requests.", text)
        self.assertIn("issue-to-pr ISSUE", text)
        self.assertIn("install --user", text)
        self.assertIn("repo install", text)
        self.assertIn("doctor", text)
        self.assertIn("scheduler", text)
        self.assertIn("queue", text)
        self.assertIn("privacy", text)
        self.assertIn("coordinate", text)
        self.assertNotIn("python3 .opencode/autodev.py", text)
        self.assertNotIn("run-real-issue", text)
        self.assertNotIn("role-check", text)

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

    def test_top_level_doctor_alias_routes_to_repo_doctor(self):
        with patch("automation.repo_setup.run_cli", return_value=5) as run_cli:
            code = autodev_cli.run(["doctor", "--json"])

        self.assertEqual(code, 5)
        run_cli.assert_called_once_with(["doctor", "--json"])

    def test_scheduler_routes_through_health_wrapper(self):
        with patch("automation.scheduler_health_cli.run_cli", return_value=6) as run_cli:
            code = autodev_cli.run(["scheduler", "status", "--json"])

        self.assertEqual(code, 6)
        run_cli.assert_called_once_with(["status", "--json"])

    def test_scheduler_worker_identity_routes_to_claim_core(self):
        with patch("automation.claim_cli.run_worker_cli", return_value=4) as run_cli:
            code = autodev_cli.run(["scheduler", "worker-id", "--set", "mega-beast"])

        self.assertEqual(code, 4)
        run_cli.assert_called_once_with(["--set", "mega-beast"])

    def test_issue_to_pr_maps_positive_issue_and_public_options_to_coordinator(self):
        with patch.object(autodev_cli, "_enable_interactive_consent_for_direct_cli"), patch.object(
            autodev_cli.opencode_entrypoint,
            "run",
            return_value=0,
        ) as run_entrypoint:
            code = autodev_cli.run(
                ["issue-to-pr", "123", "--repo", "../project", "--runtime", "opencode"]
            )

        self.assertEqual(code, 0)
        run_entrypoint.assert_called_once_with(
            [
                "coordinate",
                "--arguments",
                "123",
                "--repo",
                "../project",
                "--runtime",
                "opencode",
            ]
        )

    def test_issue_to_pr_invalid_invocation_returns_actionable_usage_guidance(self):
        error = io.StringIO()
        with redirect_stderr(error):
            code = autodev_cli.run(["issue-to-pr", "not-an-issue"])

        self.assertEqual(code, 2)
        self.assertIn("ISSUE must be a positive integer", error.getvalue())
        self.assertIn("autodev issue-to-pr --help", error.getvalue())

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
                ["queue", "status"],
            ):
                with self.subTest(command=command):
                    self.assertEqual(autodev_cli.run(command), 0)

        self.assertEqual(
            [call.args[0] for call in run_entrypoint.call_args_list],
            [
                ["status"],
                ["coordinate", "--arguments", "123"],
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

    def test_unknown_top_level_command_fails_with_discovery_guidance(self):
        error = io.StringIO()
        with redirect_stderr(error):
            code = autodev_cli.run(["definitely-not-a-command"])

        self.assertEqual(code, 2)
        self.assertIn("unknown command", error.getvalue())
        self.assertIn("autodev --help", error.getvalue())


if __name__ == "__main__":
    unittest.main()
