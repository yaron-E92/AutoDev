from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from automation import opencode_adapter_contract, opencode_install, windows_verification_contract

REPO_ROOT = Path(__file__).resolve().parents[1]


class OpenCodeInstallerTests(unittest.TestCase):
    def test_canonical_installer_copies_stable_windows_workflow_without_autodev_sha(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir).resolve()
            installed = opencode_install.install_assets(target, REPO_ROOT)
            workflow = target / opencode_install.WINDOWS_CALLER_TARGET
            first = workflow.read_text(encoding="utf-8")
            opencode_install.install_assets(target, REPO_ROOT)
            second = workflow.read_text(encoding="utf-8")

        self.assertIn(workflow, installed)
        self.assertEqual(first, second)
        self.assertIn("autodev_ref:", first)
        self.assertIn("ref: ${{ inputs.autodev_ref }}", first)
        self.assertNotIn("__AUTODEV_WORKFLOW_REF__", first)
        self.assertNotIn(opencode_install.WINDOWS_SETUP_PLACEHOLDER.strip(), first)
        self.assertIn("\npermissions:\n  contents: read\n\njobs:\n", first)
        self.assertNotIn("persist-credentials: false\n\n\n      - name: Execute Windows verification", first)

    def test_canonical_installer_renders_repository_setup_and_secret_name_mapping(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir).resolve()
            config_path = target / windows_verification_contract.CONFIG_PATH
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

            opencode_install.install_assets(target, REPO_ROOT)
            workflow = (target / opencode_install.WINDOWS_CALLER_TARGET).read_text(encoding="utf-8")

        self.assertIn('name: "Configure private packages"', workflow)
        self.assertIn("working-directory: target", workflow)
        self.assertIn("NUGET_TOKEN: ${{ secrets.REPOSITORY_PACKAGE_TOKEN }}", workflow)
        self.assertIn("Required Actions secret REPOSITORY_PACKAGE_TOKEN is unavailable", workflow)
        self.assertIn("autodev-tooling\\windows\\scripts\\configure-nuget-source.ps1", workflow)
        self.assertIn("-SourceName 'private-feed'", workflow)
        self.assertLess(workflow.index("Configure private packages"), workflow.index("Execute Windows verification"))
        self.assertIn("\npermissions:\n  contents: read\n\njobs:\n", workflow)

    def test_installer_writes_only_final_autodev_opencode_assets(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir).resolve()
            user_asset = target / ".opencode" / "custom.md"
            user_asset.parent.mkdir(parents=True)
            user_asset.write_text("preserve", encoding="utf-8")

            opencode_install.install_assets(target, REPO_ROOT)

            self.assertEqual(user_asset.read_text(encoding="utf-8"), "preserve")
            self.assertFalse((target / ".opencode" / "autodev.json").exists())
            self.assertFalse((target / ".opencode" / "autodev.py").exists())
            self.assertFalse((target / ".opencode" / "autodev.ps1").exists())
            for name in opencode_adapter_contract.COMMAND_FILES:
                text = (target / ".opencode" / "commands" / name).read_text(encoding="utf-8")
                with self.subTest(command=name):
                    self.assertNotIn(".opencode/autodev", text)
                    self.assertNotIn("__AUTODEV_PYTHON_SHELL__", text)
            for name in opencode_adapter_contract.AGENT_FILES:
                text = (target / ".opencode" / "agents" / name).read_text(encoding="utf-8")
                with self.subTest(agent=name):
                    self.assertNotIn(".opencode/autodev", text)
                    self.assertIn("Canonical AutoDev launcher", text)
                    self.assertIn("autodev", text)


if __name__ == "__main__":
    unittest.main()
