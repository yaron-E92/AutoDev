from __future__ import annotations

import io
import unittest
from contextlib import redirect_stderr, redirect_stdout

from automation import autodev_cli


class CliHelpTests(unittest.TestCase):
    @staticmethod
    def render(args: list[str]) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = autodev_cli.run(args)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_short_and_long_top_level_help_are_identical(self):
        short = self.render(["-h"])
        long = self.render(["--help"])

        self.assertEqual(short, long)
        self.assertEqual(short[0], 0)
        self.assertIn("Common workflows:", short[1])
        self.assertIn("issue-to-pr ISSUE", short[1])
        self.assertIn("Configuration:", short[1])
        self.assertIn("Runtime precedence", short[1])
        self.assertIn("AUTODEV_ROLE_RUNTIME", short[1])
        self.assertIn("opencode.json / opencode.jsonc", short[1])
        self.assertIn("Contributors:", short[1])
        self.assertIn("source-development checks", short[1])
        self.assertIn("python -m unittest discover -s tests -v", short[1])
        self.assertIn("Run 'autodev <command> --help'", short[1])

    def test_help_command_is_equivalent_to_nested_long_help(self):
        explicit = self.render(["scheduler", "install", "--help"])
        help_command = self.render(["help", "scheduler", "install"])

        self.assertEqual(explicit, help_command)
        self.assertEqual(explicit[0], 0)
        self.assertIn("--backend NAME", explicit[1])
        self.assertIn("auto|systemd-user|cron|windows-task", explicit[1])
        self.assertIn("Default: 15", explicit[1])

    def test_issue_to_pr_help_is_first_class_and_privacy_aware(self):
        code, text, error = self.render(["issue-to-pr", "-h"])

        self.assertEqual(code, 0)
        self.assertEqual(error, "")
        self.assertIn("autodev issue-to-pr ISSUE", text)
        self.assertIn("Required positive GitHub issue number", text)
        self.assertIn("--runtime NAME", text)
        self.assertIn("--semver INTENT", text)
        self.assertIn("major|minor|patch|none", text)
        self.assertIn("Default: repository/user configuration, then opencode", text)
        self.assertIn("Privacy:", text)
        self.assertIn("explicit consent grant", text)
        self.assertIn("autodev coordinate --arguments ISSUE", text)
        self.assertIn("autodev issue-to-pr 123", text)

    def test_install_and_repository_help_distinguish_setup_scopes(self):
        install = self.render(["install", "--help"])[1]
        repo = self.render(["repo", "--help"])[1]
        repo_install = self.render(["repo", "install", "--help"])[1]

        self.assertIn("User installation is separate from target-repository setup", install)
        self.assertIn("--user", install)
        self.assertIn("Required", install)
        self.assertIn("--add-to-path", install)
        self.assertIn("Repository setup owns AutoDev policy/configuration files", repo)
        self.assertIn("install", repo)
        self.assertIn("ensure-labels", repo)
        self.assertIn("doctor", repo)
        self.assertIn("--no-opencode", repo_install)

    def test_doctor_canonical_help_shows_supported_repo_alias(self):
        code, text, _ = self.render(["doctor", "-h"])

        self.assertEqual(code, 0)
        self.assertIn("autodev doctor [options]", text)
        self.assertIn("Aliases:", text)
        self.assertIn("autodev repo doctor", text)
        self.assertIn("--fix", text)
        self.assertIn("--json", text)

    def test_scheduler_parent_and_nested_notification_help_are_discoverable(self):
        scheduler = self.render(["scheduler", "-h"])[1]
        notifications = self.render(["scheduler", "notifications", "enable", "--help"])[1]

        for command in (
            "install",
            "status",
            "health",
            "notifications",
            "worker-id",
            "run-once",
            "uninstall",
        ):
            self.assertIn(command, scheduler)
        self.assertIn("--reminder-hours N", notifications)
        self.assertIn("Default: 0", notifications)

    def test_privacy_and_queue_help_explain_safe_operational_behavior(self):
        privacy = self.render(["privacy", "--help"])[1]
        consent = self.render(["privacy", "consent", "--help"])[1]
        queue = self.render(["queue", "--help"])[1]
        next_help = self.render(["queue", "next", "--help"])[1]

        self.assertIn("Inspect, grant, and revoke", privacy)
        self.assertIn("do not weaken repository policy", privacy)
        self.assertIn("interactive terminal", consent)
        self.assertIn("Headless/scheduled runs", consent)
        self.assertIn("without model calls", queue)
        self.assertIn("--dry-run", next_help)
        self.assertIn("Default:", next_help)

    def test_ux_publish_help_is_public_and_model_free(self):
        ux = self.render(["ux", "--help"])
        publish = self.render(["ux", "publish", "--help"])

        self.assertEqual(ux[0], 0)
        self.assertIn("publish", ux[1])
        self.assertIn("without model calls", ux[1])
        self.assertEqual(publish[0], 0)
        self.assertIn("autodev ux publish BUNDLE --to REFERENCE", publish[1])
        self.assertIn("--to REFERENCE", publish[1])
        self.assertNotIn("Privacy:", publish[1])

    def test_issue_specific_help_works_even_after_positional_issue(self):
        before_issue = self.render(["issue-to-pr", "--help"])
        after_issue = self.render(["issue-to-pr", "123", "--help"])

        self.assertEqual(before_issue, after_issue)

    def test_top_level_help_does_not_advertise_internal_or_retired_surfaces(self):
        text = self.render(["--help"])[1]

        for forbidden in (
            "role-check",
            " prepare ",
            " accept ",
            " stage ",
            "run-real-issue",
            ".opencode/autodev.py",
            "workflow-stage",
        ):
            self.assertNotIn(forbidden, text)

    def test_unknown_help_topic_is_actionable(self):
        code, output, error = self.render(["does-not-exist", "--help"])

        self.assertEqual(code, 2)
        self.assertEqual(output, "")
        self.assertIn("no public help topic", error)
        self.assertIn("autodev --help", error)


if __name__ == "__main__":
    unittest.main()
