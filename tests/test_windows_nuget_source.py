from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "windows" / "scripts" / "configure-nuget-source.ps1"


class WindowsNuGetSourceTests(unittest.TestCase):
    def test_helper_is_generic_and_reads_only_the_canonical_token_environment_variable(self):
        text = SCRIPT.read_text(encoding="utf-8")

        self.assertIn("$env:NUGET_TOKEN", text)
        self.assertIn("[string]$SourceUrl", text)
        self.assertIn("[string]$SourceName", text)
        self.assertIn("[string]$Username", text)
        self.assertNotIn("Yaref92", text)
        self.assertNotIn("nuget.pkg.github.com", text)

    @unittest.skipUnless(shutil.which("pwsh"), "PowerShell is required for the execution test")
    def test_helper_replaces_source_using_mapped_token(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            log = temp / "dotnet.log"
            fake_dotnet = temp / "dotnet.cmd"
            fake_dotnet.write_text(
                "@echo off\r\necho %*>>\"%AUTODEV_TEST_DOTNET_LOG%\"\r\nexit /b 0\r\n",
                encoding="utf-8",
            )
            env = os.environ.copy()
            env["PATH"] = str(temp) + os.pathsep + env.get("PATH", "")
            env["AUTODEV_TEST_DOTNET_LOG"] = str(log)
            env["NUGET_TOKEN"] = "mapped-test-token"

            completed = subprocess.run(
                [
                    "pwsh",
                    "-NoProfile",
                    "-File",
                    str(SCRIPT),
                    "-SourceUrl",
                    "https://packages.example.test/index.json",
                    "-SourceName",
                    "private-feed",
                    "-Username",
                    "package-user",
                ],
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                check=False,
                env=env,
            )
            calls = log.read_text(encoding="utf-8")

        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn("nuget remove source private-feed", calls)
        self.assertIn("nuget add source https://packages.example.test/index.json", calls)
        self.assertIn("--username package-user", calls)
        self.assertIn("--password mapped-test-token", calls)
        self.assertIn("--valid-authentication-types basic", calls)

    @unittest.skipUnless(shutil.which("pwsh"), "PowerShell is required for the execution test")
    def test_helper_fails_before_dotnet_when_token_is_missing(self):
        env = os.environ.copy()
        env.pop("NUGET_TOKEN", None)

        completed = subprocess.run(
            [
                "pwsh",
                "-NoProfile",
                "-File",
                str(SCRIPT),
                "-SourceUrl",
                "https://packages.example.test/index.json",
                "-SourceName",
                "private-feed",
                "-Username",
                "package-user",
            ],
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
            env=env,
        )

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("NUGET_TOKEN is required", completed.stdout + completed.stderr)


if __name__ == "__main__":
    unittest.main()
