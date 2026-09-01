from __future__ import annotations

from automation import autodev_cli, privacy_grant_cli, privacy_grant_hooks, repair_budget_resume

from automation import windows_verification_hooks

import unittest
from unittest import mock

from automation import opencode_entrypoint


class PrivacyCliEntrypointTests(unittest.TestCase):
    def test_public_privacy_command_does_not_boot_opencode_entrypoint(self):
        with (
            mock.patch(
                "automation.autodev_cli.privacy_grant_cli.run_cli",
                return_value=23,
            ) as run_cli,
            mock.patch(
                "automation.autodev_cli.opencode_entrypoint.run",
                side_effect=AssertionError("privacy command must not boot OpenCode"),
            ),
        ):
            result = autodev_cli.run(["privacy", "status", "--json"])

        self.assertEqual(result, 23)
        run_cli.assert_called_once_with(["status", "--json"])

    def test_privacy_command_routes_to_privacy_grant_cli(self):
        install_targets = (
            "ci_outcomes.install",
            "pr_head_sync.install",
            "repair_budget_resume.install_opencode_resume_hooks",
            "windows_verification_hooks.install_opencode_hooks",
            "windows_semantic_order.install",
            "context_optimization.install",
            "privacy_consent.install",
            "privacy_grant_hooks.install",
        )
        patches = [
            mock.patch(f"automation.opencode_entrypoint.{target}")
            for target in install_targets
        ]
        started = [patch.start() for patch in patches]
        self.addCleanup(lambda: [patch.stop() for patch in reversed(patches)])

        with mock.patch(
            "automation.opencode_entrypoint.privacy_grant_cli.run_cli",
            return_value=17,
        ) as run_cli:
            result = opencode_entrypoint.run(["privacy", "status", "--json"])

        self.assertEqual(result, 17)
        run_cli.assert_called_once_with(["status", "--json"])
        started[-1].assert_called_once_with(run_consent=True)


if __name__ == "__main__":
    unittest.main()
