from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def remove_test_method(text: str, name: str) -> str:
    pattern = re.compile(
        rf"\n    def {re.escape(name)}\(self.*?(?=\n    def |\n\nif __name__ ==)",
        re.S,
    )
    text, count = pattern.subn("", text, count=1)
    if count != 1:
        raise SystemExit(f"expected test method not found: {name}")
    return text


def write_installers() -> None:
    path = ROOT / "tests" / "test_opencode_installers.py"
    path.write_text(
        '''from __future__ import annotations\n\nimport json\nimport tempfile\nimport unittest\nfrom pathlib import Path\n\nfrom automation import opencode_adapter_contract, opencode_install, windows_verification_contract\n\nREPO_ROOT = Path(__file__).resolve().parents[1]\n\n\nclass OpenCodeInstallerTests(unittest.TestCase):\n    def test_canonical_installer_copies_stable_windows_workflow_without_autodev_sha(self):\n        with tempfile.TemporaryDirectory() as temp_dir:\n            target = Path(temp_dir).resolve()\n            installed = opencode_install.install_assets(target, REPO_ROOT)\n            workflow = target / opencode_install.WINDOWS_CALLER_TARGET\n            first = workflow.read_text(encoding="utf-8")\n            opencode_install.install_assets(target, REPO_ROOT)\n            second = workflow.read_text(encoding="utf-8")\n\n        self.assertIn(workflow, installed)\n        self.assertEqual(first, second)\n        self.assertIn("autodev_ref:", first)\n        self.assertIn("ref: ${{ inputs.autodev_ref }}", first)\n        self.assertNotIn("__AUTODEV_WORKFLOW_REF__", first)\n        self.assertNotIn(opencode_install.WINDOWS_SETUP_PLACEHOLDER.strip(), first)\n\n    def test_canonical_installer_renders_repository_setup_and_secret_name_mapping(self):\n        with tempfile.TemporaryDirectory() as temp_dir:\n            target = Path(temp_dir).resolve()\n            config_path = target / windows_verification_contract.CONFIG_PATH\n            config_path.parent.mkdir(parents=True)\n            config_path.write_text(\n                json.dumps(\n                    {\n                        "version": 1,\n                        "enabled": True,\n                        "commands": [{"name": "test", "command": "dotnet test App.Tests.csproj"}],\n                        "setup": {\n                            "name": "Configure private packages",\n                            "command": (\n                                '& "$env:GITHUB_WORKSPACE\\\\autodev-tooling\\\\windows\\\\scripts\\\\'\n                                "configure-nuget-source.ps1\\\" -SourceUrl "\n                                "'https://packages.example.test/index.json' -SourceName 'private-feed' "\n                                "-Username 'package-user'"\n                            ),\n                            "secret_env": {"NUGET_TOKEN": "REPOSITORY_PACKAGE_TOKEN"},\n                        },\n                    }\n                ),\n                encoding="utf-8",\n            )\n\n            opencode_install.install_assets(target, REPO_ROOT)\n            workflow = (target / opencode_install.WINDOWS_CALLER_TARGET).read_text(encoding="utf-8")\n\n        self.assertIn('name: "Configure private packages"', workflow)\n        self.assertIn("working-directory: target", workflow)\n        self.assertIn("NUGET_TOKEN: ${{ secrets.REPOSITORY_PACKAGE_TOKEN }}", workflow)\n        self.assertIn("Required Actions secret REPOSITORY_PACKAGE_TOKEN is unavailable", workflow)\n        self.assertIn("autodev-tooling\\\\windows\\\\scripts\\\\configure-nuget-source.ps1", workflow)\n        self.assertIn("-SourceName 'private-feed'", workflow)\n        self.assertLess(workflow.index("Configure private packages"), workflow.index("Execute Windows verification"))\n\n    def test_installer_writes_only_final_autodev_opencode_assets(self):\n        with tempfile.TemporaryDirectory() as temp_dir:\n            target = Path(temp_dir).resolve()\n            user_asset = target / ".opencode" / "custom.md"\n            user_asset.parent.mkdir(parents=True)\n            user_asset.write_text("preserve", encoding="utf-8")\n\n            opencode_install.install_assets(target, REPO_ROOT)\n\n            self.assertEqual(user_asset.read_text(encoding="utf-8"), "preserve")\n            self.assertFalse((target / ".opencode" / "autodev.json").exists())\n            self.assertFalse((target / ".opencode" / "autodev.py").exists())\n            self.assertFalse((target / ".opencode" / "autodev.ps1").exists())\n            for name in opencode_adapter_contract.COMMAND_FILES:\n                text = (target / ".opencode" / "commands" / name).read_text(encoding="utf-8")\n                with self.subTest(command=name):\n                    self.assertNotIn(".opencode/autodev", text)\n                    self.assertNotIn("__AUTODEV_PYTHON_SHELL__", text)\n            for name in opencode_adapter_contract.AGENT_FILES:\n                text = (target / ".opencode" / "agents" / name).read_text(encoding="utf-8")\n                with self.subTest(agent=name):\n                    self.assertNotIn(".opencode/autodev", text)\n                    self.assertIn("Canonical AutoDev launcher", text)\n                    self.assertIn("autodev", text)\n\n\nif __name__ == "__main__":\n    unittest.main()\n''',
        encoding="utf-8",
    )


def write_interactive() -> None:
    path = ROOT / "tests" / "test_opencode_interactive_consent.py"
    path.write_text(
        '''from __future__ import annotations\n\nimport tempfile\nimport unittest\nfrom pathlib import Path\n\nfrom automation import opencode_install\n\nREPO_ROOT = Path(__file__).resolve().parents[1]\nINTERACTIVE_COMMANDS = (\n    "autodev-issue-to-pr.md",\n    "autodev-resume.md",\n    "autodev-read.md",\n    "autodev-plan.md",\n    "autodev-implement.md",\n    "autodev-fix.md",\n    "autodev-verify.md",\n)\n\n\nclass OpenCodeInteractiveConsentTests(unittest.TestCase):\n    def test_installed_commands_use_first_class_cli_and_opt_into_interactive_consent(self):\n        with tempfile.TemporaryDirectory() as temp_dir:\n            target = Path(temp_dir).resolve()\n            opencode_install.install_assets(target, REPO_ROOT)\n            rendered = {\n                name: (target / ".opencode" / "commands" / name).read_text(encoding="utf-8")\n                for name in INTERACTIVE_COMMANDS\n            }\n\n        for name, content in rendered.items():\n            with self.subTest(command=name):\n                self.assertIn("--interactive-consent", content)\n                self.assertIn("!`autodev ", content)\n                self.assertNotIn(".opencode/autodev", content)\n                self.assertNotIn("__AUTODEV_PYTHON_SHELL__", content)\n\n\nif __name__ == "__main__":\n    unittest.main()\n''',
        encoding="utf-8",
    )


def write_hardening() -> None:
    path = ROOT / "tests" / "test_opencode_local_coordinator_hardening.py"
    path.write_text(
        '''import tempfile\nimport unittest\nfrom pathlib import Path\n\nfrom automation import opencode_adapter_contract, opencode_install\n\nREPO_ROOT = Path(__file__).resolve().parents[1]\n\n\nclass OpenCodeLocalCoordinatorHardeningTests(unittest.TestCase):\n    def test_canonical_installed_coordinator_is_closed_world_and_uses_autodev_launcher(self):\n        with tempfile.TemporaryDirectory() as temp_dir:\n            target = Path(temp_dir)\n            opencode_install.install_assets(target, REPO_ROOT)\n            coordinator = (target / ".opencode" / "agents" / "autodev-coordinator.md").read_text(encoding="utf-8")\n\n        self.assertIn('permission:\\n  "*": deny', coordinator)\n        self.assertNotIn(".opencode/autodev", coordinator)\n        self.assertIn('"autodev stage *": allow', coordinator)\n        self.assertIn("Canonical AutoDev launcher", coordinator)\n        self.assertIn("installed `autodev` command", coordinator)\n        self.assertIn("do not probe", coordinator.casefold())\n        self.assertIn("Unrelated built-in, plugin, or MCP tools are denied by default", coordinator)\n\n    def test_all_canonical_installed_role_agents_use_first_class_launcher(self):\n        with tempfile.TemporaryDirectory() as temp_dir:\n            target = Path(temp_dir)\n            opencode_install.install_assets(target, REPO_ROOT)\n            for name in opencode_adapter_contract.AGENT_FILES:\n                text = (target / ".opencode" / "agents" / name).read_text(encoding="utf-8")\n                with self.subTest(agent=name):\n                    self.assertNotIn(".opencode/autodev", text)\n                    self.assertIn("Canonical AutoDev launcher", text)\n                    self.assertIn("installed `autodev` command", text)\n                    self.assertIn("do not probe", text.casefold())\n\n\nif __name__ == "__main__":\n    unittest.main()\n''',
        encoding="utf-8",
    )


def generic_test_updates() -> None:
    for path in (ROOT / "tests").glob("test_*.py"):
        text = path.read_text(encoding="utf-8")
        text = text.replace("python3 .opencode/autodev.py", "autodev")
        text = text.replace("python .opencode/autodev.py", "autodev")
        text = re.sub(r',?\n\s*python_command="python3",', ",", text)
        text = re.sub(r',?\n\s*python_command="python",', ",", text)
        path.write_text(text, encoding="utf-8")

    repo_setup = ROOT / "tests" / "test_repo_setup.py"
    text = repo_setup.read_text(encoding="utf-8")
    text = remove_test_method(text, "test_legacy_mixed_layout_migrates_without_touching_user_content_or_active_run")
    repo_setup.write_text(text, encoding="utf-8")

    boundary = ROOT / "tests" / "test_opencode_role_boundary_contracts.py"
    text = boundary.read_text(encoding="utf-8")
    text = remove_test_method(text, "test_legacy_llm_coordinator_commands_still_fail_closed_for_manual_compatibility")
    boundary.write_text(text, encoding="utf-8")


def main() -> int:
    write_installers()
    write_interactive()
    write_hardening()
    generic_test_updates()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
