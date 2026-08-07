import unittest
from pathlib import Path

from automation import workflow_verify


REPO_ROOT = Path(__file__).resolve().parents[1]


class WorkflowVerifyTests(unittest.TestCase):
    def test_windows_dispatches_to_powershell_verifier(self):
        command = workflow_verify.verification_command("backend,maui", REPO_ROOT, platform="nt")

        self.assertEqual(command[:3], ["pwsh", "-NoProfile", "-File"])
        self.assertTrue(command[3].endswith("windows/scripts/codex-verify.ps1"))
        self.assertEqual(command[-2:], ["-Profiles", "backend,maui"])

    def test_linux_dispatches_to_bash_verifier(self):
        command = workflow_verify.verification_command("python", REPO_ROOT, platform="posix")

        self.assertEqual(command[0], "bash")
        self.assertTrue(command[1].endswith("linux/scripts/codex-verify.sh"))
        self.assertEqual(command[-2:], ["--profiles", "python"])

    def test_shared_profile_uses_portable_dispatcher(self):
        profile = (REPO_ROOT / "codex-profiles.json").read_text(encoding="utf-8")

        self.assertIn("automation.workflow_verify", profile)
        self.assertNotIn("codex-verify.ps1", profile)
        self.assertNotIn("codex-verify.sh", profile)


if __name__ == "__main__":
    unittest.main()
