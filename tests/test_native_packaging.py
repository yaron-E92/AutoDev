from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from automation import msi_reproducibility, native_linux, native_packaging, native_windows


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
        self.assertIn('Key="Software\\AutoDev\\Payload"', first)
        self.assertIn('Key="Software\\AutoDev\\Directories"', first)
        self.assertIn('KeyPath="yes"', first)
        self.assertIn('<RemoveFolder ', first)
        self.assertIn('Directory="INSTALLFOLDER" On="uninstall"', first)
        self.assertIn('Directory="AutoDevProgramsFolder" On="uninstall"', first)
        self.assertNotIn('Source="', first.split('KeyPath="yes"', 1)[0].splitlines()[-1])
        self.assertIn('Name="PATH"', first)
        self.assertIn('System="no"', first)
        self.assertIn('Value="[INSTALLFOLDER]"', first)
        self.assertIn('Directory Id="LocalAppDataFolder"', first)
        self.assertIn('Directory Id="AutoDevProgramsFolder" Name="Programs"', first)
        self.assertNotIn(".autodev-run", first)
        self.assertNotIn("privacy-grants", first)

    def test_windows_file_components_use_registry_not_files_as_key_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            payload = Path(temp_dir) / "payload"
            payload.mkdir()
            (payload / "autodev.exe").write_bytes(b"exe")
            source = native_windows.render_wix_source(
                payload,
                "v1.2.3",
                "abcdef1234567890abcdef1234567890abcdef12",
            )

        file_line = next(line for line in source.splitlines() if "<File " in line)
        payload_registry = next(
            line
            for line in source.splitlines()
            if 'Key="Software\\AutoDev\\Payload"' in line
        )
        self.assertNotIn("KeyPath=", file_line)
        self.assertIn('KeyPath="yes"', payload_registry)

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

    def test_msi_filetime_conversion_is_deterministic(self) -> None:
        epoch = 1_234_567_890
        first = msi_reproducibility.filetime_from_unix(epoch)
        second = msi_reproducibility.filetime_from_unix(epoch)
        self.assertEqual(first.dwLowDateTime, second.dwLowDateTime)
        self.assertEqual(first.dwHighDateTime, second.dwHighDateTime)
        self.assertNotEqual((first.dwLowDateTime, first.dwHighDateTime), (0, 0))

    def test_debian_metadata_declares_runtime_and_no_scheduler_side_effects(self) -> None:
        control = native_linux.deb_control("v3.4.5")
        self.assertIn("Version: 3.4.5", control)
        self.assertIn("Architecture: amd64", control)
        self.assertIn("Depends: libc6, git, gh", control)
        self.assertIn("Homepage: https://github.com/yaron-E92/AutoDev", control)
        self.assertIn("X-AutoDev-License: NOASSERTION", control)
        self.assertNotIn("systemd", control.casefold())
        self.assertNotIn("cron", control.casefold())
        self.assertEqual(native_linux.deb_artifact_name("v3.4.5"), "autodev_3.4.5_amd64.deb")

    def test_rpm_metadata_declares_runtime_identity_and_reproducibility_controls(self) -> None:
        spec = native_linux.rpm_spec("v3.4.5")
        self.assertIn("Version:        3.4.5", spec)
        self.assertIn("BuildArch:      x86_64", spec)
        self.assertIn("License:        NOASSERTION", spec)
        self.assertIn("Requires:       glibc", spec)
        self.assertIn("Requires:       git", spec)
        self.assertIn("Requires:       gh", spec)
        self.assertIn("/opt/autodev", spec)
        self.assertIn("/usr/bin/autodev", spec)
        self.assertNotIn("systemctl", spec)
        command = native_linux.rpm_build_command(Path("/tmp/build root"), Path("/tmp/spec file"), 123456789)
        self.assertIn("use_source_date_epoch_as_buildtime 1", command)
        self.assertIn("build_mtime_policy clamp_to_source_date_epoch", command)
        self.assertIn("_buildhost autodev.invalid", command)
        self.assertEqual(native_linux.rpm_artifact_name("v3.4.5"), "autodev-3.4.5-1.x86_64.rpm")

    def test_payload_tar_is_byte_reproducible_and_preserves_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            payload = root / "payload"
            payload.mkdir()
            executable = payload / "autodev"
            executable.write_bytes(b"binary")
            executable.chmod(0o755)
            internal = payload / "_internal"
            internal.mkdir()
            (internal / "data.txt").write_text("data\n", encoding="utf-8")
            if os.name != "nt":
                (payload / "alias").symlink_to("autodev")
            first = root / "a.tar.gz"
            second = root / "b.tar.gz"
            native_linux.write_payload_tar(payload, first, "v1.2.3", 123456789)
            native_linux.write_payload_tar(payload, second, "v1.2.3", 123456789)

            self.assertEqual(first.read_bytes(), second.read_bytes())


if __name__ == "__main__":
    unittest.main()
