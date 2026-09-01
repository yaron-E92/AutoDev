from __future__ import annotations

import unittest

from automation import headless_environment


class HeadlessEnvironmentTests(unittest.TestCase):
    def test_disables_git_github_and_ssh_prompts(self) -> None:
        result = headless_environment.environment({"PATH": "/bin"})
        self.assertEqual(result["GIT_TERMINAL_PROMPT"], "0")
        self.assertEqual(result["GCM_INTERACTIVE"], "Never")
        self.assertEqual(result["GH_PROMPT_DISABLED"], "1")
        self.assertIn("BatchMode=yes", result["GIT_SSH_COMMAND"])

    def test_preserves_existing_ssh_command_while_adding_batch_mode(self) -> None:
        result = headless_environment.environment(
            {"GIT_SSH_COMMAND": "ssh -i /tmp/scheduler-key"}
        )
        self.assertIn("-i /tmp/scheduler-key", result["GIT_SSH_COMMAND"])
        self.assertIn("BatchMode=yes", result["GIT_SSH_COMMAND"])


if __name__ == "__main__":
    unittest.main()
