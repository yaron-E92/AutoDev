from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from automation import native_packaging, native_windows


class NativePackagingTests(unittest.TestCase):
    def test_package_version_strips_only_semver_v_prefix(self) -> None:
        self.assertEqual(native_packaging.package_version("v1.10.2"), "1.10.2")

    def test_build_info_is_stable_and_tied_to_source_identity(self) -> None:
        text = native_packaging.build_info_text(
            "v2.3.4",
            "abcdef1234567890abcdef1234567890abcdef12",
        )
        self.assertEqual(
            json.loads(text),
            {
                "schema_version": 1,
                "version": "v2.3.4",
                "commit_sha": "abcdef1234567890abcdef1234567890abcdef12",
            },
        )
        self.assertTrue(text.endswith("\n"))

    def test_pyinstaller_command_is_onedir_and_embeds_product_resources(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir) / "repo with spaces"
            out = Path(temp_dir) / "output with spaces"
            work = Path(temp_dir) / "work"
            (repo / "packaging").mkdir(parents=True)
            (repo / "packaging" / "autodev_entry.py").write_text("pass\n", encoding="utf-8")
            for relative in ("integrations", "promptTemplates", "agentFiles", "docs"):
                (repo / relative).mkdir(parents=True)
            for relative in ("README.md", "CONTRIBUTING.md", "codex-profiles.json"):
                (repo / relative).write_text("fixture\n", encoding="utf-8")
            build_info = work / "autodev-build.json"

            command = native_packaging.pyinstaller_command(
                repo,
                out,
                work,
                build_info,
                windows=False,
            )

        self.assertIn("--onedir", command)
        self.assertIn("--noupx", command)
        self.assertIn("--collect-submodules", command)
        self.assertIn("automation", command)
        self.assertIn("area_reader", command)
        data_values = [command[index + 1] for index, value in enumerate(command[:-1]) if value == "--add-data"]
        self.assertTrue(any("integrations" in value for value in data_values))
        self.assertTrue(any("autodev-build.json" in value for value in data_values))
        # Commands are argument arrays rather than shell strings, so paths containing spaces remain atomic.
        self.assertIn(str(repo), command)
        self.assertIn(str(out), command)

    def test_windows_add_data_uses_windows_separator(self) -> None:
        value = native_packaging._data_argument(Path(r"C:\Auto Dev\docs"), "docs", windows=True)
        self.assertIn(";docs", value)

    def test_windows_msi_authoring_is_per_user_upgrade_safe_and_path_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            payload = Path(temp_dir) / "payload with spaces"
            (payload / "_internal" / "automation").mkdir(parents=True)
            (payload / "autodev.exe").write_bytes(b"exe")
            (payload / "_internal" / "automation" / "module.pyc").write_bytes(b"module")
            commit = "abcdef1234567890abcdef1234567890abcdef12"

            first = native_windows.render_wix_source(payload, "v1.2.3", commit)
            second = native_windows.render_wix_source(payload, "v1.2.3", commit)

        self.assertEqual(first, second)
        self.assertIn('InstallScope="perUser"', first)
        self.assertIn('InstallPrivileges="limited"', first)
        self.assertIn('Schedule="afterInstallInitialize"', first)
        self.assertIn('Root="HKCU" Key="Software\\AutoDev"', first)
        self.assertIn('Name="PATH"', first)
        self.assertIn('System="no"', first)
        self.assertIn('Value="[INSTALLFOLDER]"', first)
        self.assertIn('Directory Id="LocalAppDataFolder"', first)
        self.assertIn('Directory Id="AutoDevProgramsFolder" Name="Programs"', first)
        self.assertNotIn(".autodev-run", first)
        self.assertNotIn("privacy-grants", first)

    def test_windows_msi_identity_is_stable_for_same_release_and_changes_by_version(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            payload = Path(temp_dir) / "payload"
            payload.mkdir()
            (payload / "autodev.exe").write_bytes(b"exe")
            commit = "abcdef1234567890abcdef1234567890abcdef12"
            same_a = native_windows.render_wix_source(payload, "v1.2.3", commit)
            same_b = native_windows.render_wix_source(payload, "v1.2.3", commit)
            newer = native_windows.render_wix_source(payload, "v1.2.4", commit)

        self.assertEqual(same_a, same_b)
        self.assertNotEqual(same_a, newer)
        self.assertEqual(native_windows.artifact_name("v1.2.3"), "AutoDev-1.2.3-Setup.msi")


if __name__ == "__main__":
    unittest.main()
