from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from automation import local_verification, workflow_prompts, workflow_verification
from automation.workflow_contract import FAILURE_SETUP, WorkflowStageError
from automation.workflow_storage import read_state, write_json, write_state


REPO_ROOT = Path(__file__).resolve().parents[1]


class LocalVerificationPlatformTests(unittest.TestCase):
    def test_shipped_default_is_platform_neutral_on_linux(self):
        profiles = REPO_ROOT / "codex-profiles.json"
        profiles_csv, command, _ = workflow_prompts.resolve_profiles(
            [],
            profiles,
            explicit_profiles="",
            explicit_local_check="",
            explicit_stack_context="",
            autodev_root=REPO_ROOT,
            platform="linux",
            which=lambda _name: None,
        )

        self.assertEqual(profiles_csv, "auto")
        self.assertEqual(command, local_verification.BUILTIN_LOCAL_CHECK)
        self.assertNotIn("pwsh", command.casefold())
        self.assertNotIn("\\codex-verify.ps1", command)

    def test_platform_map_can_keep_powershell_for_windows_when_configured(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            profiles = root / "profiles.json"
            profiles.write_text(
                json.dumps(
                    {
                        "defaultProfile": "auto",
                        "verifyCommandTemplate": {
                            "windows": "pwsh -NoProfile -Command \"Write-Host ok\"",
                            "linux": "autodev verify-local",
                        },
                        "profiles": {},
                    }
                ),
                encoding="utf-8",
            )

            _, windows, _ = workflow_prompts.resolve_profiles(
                [],
                profiles,
                explicit_profiles="",
                explicit_local_check="",
                explicit_stack_context="",
                autodev_root=root,
                platform="win32",
                which=lambda name: "C:/tools/pwsh.exe" if name == "pwsh" else None,
            )
            _, linux, _ = workflow_prompts.resolve_profiles(
                [],
                profiles,
                explicit_profiles="",
                explicit_local_check="",
                explicit_stack_context="",
                autodev_root=root,
                platform="linux",
                which=lambda _name: None,
            )

        self.assertTrue(windows.startswith("pwsh "))
        self.assertEqual(linux, local_verification.BUILTIN_LOCAL_CHECK)

    def test_explicit_linux_powershell_check_is_allowed_when_pwsh_exists(self):
        command = "pwsh -NoProfile -Command \"Write-Host ok\""
        local_verification.preflight_local_check(
            command,
            explicit=True,
            platform="linux",
            which=lambda name: "/usr/bin/pwsh" if name == "pwsh" else None,
        )

    def test_missing_explicit_pwsh_is_setup_failure(self):
        command = "pwsh -NoProfile -Command \"Write-Host ok\""
        with self.assertRaises(WorkflowStageError) as caught:
            local_verification.preflight_local_check(
                command,
                explicit=True,
                platform="linux",
                which=lambda _name: None,
            )

        self.assertEqual(caught.exception.classification, FAILURE_SETUP)
        self.assertIn("pwsh", str(caught.exception))
        self.assertIn("unavailable", str(caught.exception))

    def test_goldilocks_mixed_path_reproduction_is_setup_not_code_repairable(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            profiles = root / "codex-profiles.json"
            profiles.write_text("{}\n", encoding="utf-8")
            command = 'pwsh -File "/home/yaref92/codex-tools\\codex-verify.ps1" -Profiles "auto"'

            with self.assertRaises(WorkflowStageError) as caught:
                local_verification.preflight_local_check(
                    command,
                    explicit=False,
                    profiles_path=profiles,
                    autodev_root=root,
                    platform="linux",
                    which=lambda name: "/usr/bin/pwsh" if name == "pwsh" else None,
                )

        self.assertEqual(caught.exception.classification, FAILURE_SETUP)
        self.assertIn("Windows-only", str(caught.exception))
        self.assertIn(command, str(caught.exception))

    def test_builtin_runner_executes_recommended_argv_without_shell(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = repo / ".autodev-run" / "current"
            current.mkdir(parents=True)
            write_json(
                current / "verification-command-groups.json",
                [
                    {
                        "name": "unit",
                        "manual": False,
                        "commands": [
                            {
                                "label": "Run unit verification",
                                "cwd": ".",
                                "argv": ["python", "-m", "unittest"],
                                "optional": False,
                            }
                        ],
                    }
                ],
            )
            write_json(
                current / "recommended-command-groups.json",
                {"recommended_command_groups": ["unit"]},
            )
            calls = []

            def runner(argv, **kwargs):
                calls.append((list(argv), kwargs))
                return SimpleNamespace(returncode=0, stdout="tests passed\n", stderr="")

            result = local_verification.run_recommended_verification(
                repo,
                current,
                runner=runner,
                which=lambda name: "/usr/bin/python" if name == "python" else None,
            )

        self.assertEqual(result.returncode, 0)
        self.assertIn("tests passed", result.output)
        self.assertEqual(calls[0][0], ["python", "-m", "unittest"])
        self.assertNotIn("shell", calls[0][1])

    def test_builtin_launched_test_failure_remains_code_repairable_signal(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = repo / ".autodev-run" / "current"
            current.mkdir(parents=True)
            write_json(
                current / "verification-command-groups.json",
                [
                    {
                        "name": "unit",
                        "manual": False,
                        "commands": [
                            {
                                "label": "Run tests",
                                "cwd": ".",
                                "argv": ["dotnet", "test"],
                                "optional": False,
                            }
                        ],
                    }
                ],
            )
            write_json(
                current / "recommended-command-groups.json",
                {"recommended_command_groups": ["unit"]},
            )

            result = local_verification.run_recommended_verification(
                repo,
                current,
                runner=lambda *_args, **_kwargs: SimpleNamespace(
                    returncode=1,
                    stdout="test failure\n",
                    stderr="",
                ),
                which=lambda name: "/usr/bin/dotnet" if name == "dotnet" else None,
            )

        self.assertEqual(result.returncode, 1)
        self.assertIn("test failure", result.output)

    def test_local_check_setup_failure_writes_no_fixer_repair_artifact(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = repo / ".autodev-run" / "current"
            current.mkdir(parents=True)
            state = {
                "LocalCheck": "pwsh -NoProfile -Command \"Write-Host ok\"",
                "LocalCheckSource": "explicit",
                "ProfilesPath": str(REPO_ROOT / "codex-profiles.json"),
                "RunDir": str(current),
                "VerificationProofVersion": 1,
            }
            write_state(current, state)

            with patch.object(workflow_verification.shutil, "which", return_value=None):
                with self.assertRaises(WorkflowStageError) as caught:
                    workflow_verification.run_local_check(
                        repo,
                        current,
                        read_state(current),
                        REPO_ROOT,
                    )

            updated = read_state(current)

        self.assertEqual(caught.exception.classification, FAILURE_SETUP)
        self.assertEqual(updated["Status"], "LocalCheckSetupFailed")
        self.assertEqual(updated["LocalCheckFailureClassification"], FAILURE_SETUP)
        self.assertFalse((current / "local-repair.md").exists())
        self.assertIn("pwsh", (current / "local-check.log").read_text(encoding="utf-8"))

    def test_legacy_linux_run_refreshes_old_shipped_pwsh_command_on_resume(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = repo / ".autodev-run" / "current"
            current.mkdir(parents=True)
            source = repo / "kept-edit.txt"
            source.write_text("implementation stays\n", encoding="utf-8")
            legacy = 'pwsh -File "/home/yaref92/codex-tools\\codex-verify.ps1" -Profiles "auto"'
            state = {
                "LocalCheck": legacy,
                "ProfilesCsv": "auto",
                "ProfilesPath": str(REPO_ROOT / "codex-profiles.json"),
                "RunDir": str(current),
            }

            refreshed, provenance, _ = local_verification.refreshed_local_check(
                state,
                REPO_ROOT,
                platform="linux",
                which=lambda _name: None,
            )

            self.assertEqual(refreshed, local_verification.BUILTIN_LOCAL_CHECK)
            self.assertEqual(provenance, "profile")
            self.assertEqual(source.read_text(encoding="utf-8"), "implementation stays\n")


if __name__ == "__main__":
    unittest.main()
