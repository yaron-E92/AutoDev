from __future__ import annotations

from automation import opencode_adapter_contract

from automation import opencode_adapter_cli

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from automation import opencode_install, windows_verification


REPO_ROOT = Path(__file__).resolve().parents[1]


class OpenCodeInstallerTests(unittest.TestCase):
    def test_canonical_installer_copies_stable_windows_workflow_without_autodev_sha(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir).resolve()
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
        self.assertNotIn(opencode_install.WINDOWS_SETUP_PLACEHOLDER.strip(), first)

    def test_canonical_installer_renders_repository_setup_and_secret_name_mapping(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir).resolve()
            config_path = target / windows_verification.CONFIG_PATH
            config_path.parent.mkdir(parents=True)
            config_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "enabled": True,
                        "commands": [{"name": "test", "command": "dotnet test App.Tests.csproj"}],
                        "setup": {
                            "name": "Configure private packages",
                            "command": (
                                '& "$env:GITHUB_WORKSPACE\\autodev-tooling\\windows\\scripts\\'
                                "configure-nuget-source.ps1\" -SourceUrl "
                                "'https://packages.example.test/index.json' -SourceName 'private-feed' "
                                "-Username 'package-user'"
                            ),
                            "secret_env": {"NUGET_TOKEN": "REPOSITORY_PACKAGE_TOKEN"},
                        },
                    }
                ),
                encoding="utf-8",
            )

            opencode_install.install_assets(target, REPO_ROOT, python_command="python3")
            workflow = (target / opencode_install.WINDOWS_CALLER_TARGET).read_text(encoding="utf-8")

        self.assertIn('name: "Configure private packages"', workflow)
        self.assertIn("working-directory: target", workflow)
        self.assertIn("NUGET_TOKEN: ${{ secrets.REPOSITORY_PACKAGE_TOKEN }}", workflow)
        self.assertIn("Required Actions secret REPOSITORY_PACKAGE_TOKEN is unavailable", workflow)
        self.assertIn("autodev-tooling\\windows\\scripts\\configure-nuget-source.ps1", workflow)
        self.assertIn("-SourceName 'private-feed'", workflow)
        self.assertLess(workflow.index("Configure private packages"), workflow.index("Execute Windows verification"))

    def test_canonical_installer_removes_generic_opencode_config_and_modernizes_agents(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir).resolve()
            user_asset = target / ".opencode" / "custom.md"
            user_asset.parent.mkdir(parents=True)
            user_asset.write_text("preserve", encoding="utf-8")

            opencode_install.install_assets(target, REPO_ROOT, python_command="python3")

            self.assertFalse((target / ".opencode" / "autodev.json").exists())
            self.assertEqual(user_asset.read_text(encoding="utf-8"), "preserve")
            self.assertTrue((target / ".opencode" / "autodev.py").is_file())
            self.assertTrue((target / ".opencode" / "autodev.ps1").is_file())
            for name in opencode_adapter_contract.AGENT_FILES:
                text = (target / ".opencode" / "agents" / name).read_text(encoding="utf-8")
                with self.subTest(agent=name):
                    self.assertNotIn(".opencode/autodev.json", text)
                    self.assertIn("Canonical AutoDev launcher", text)
                    self.assertIn("autodev", text)

    def test_deprecated_adapter_install_delegates_to_complete_canonical_installer(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir).resolve()
            stderr = io.StringIO()
            stdout = io.StringIO()
            with redirect_stderr(stderr), redirect_stdout(stdout):
                code = opencode_adapter_cli.run(
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
            generic_config_exists = (target / ".opencode" / "autodev.json").exists()

        self.assertEqual(code, 0)
        self.assertTrue(workflow_exists)
        self.assertFalse(generic_config_exists)
        self.assertIn("DEPRECATED", stderr.getvalue())
        self.assertIn("automation.opencode_install", stderr.getvalue())
        self.assertIn("Installed", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
