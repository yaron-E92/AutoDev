from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from automation import opencode_adapter, opencode_resume, run_manifest, windows_verification, workflow_stages


HEAD = "a" * 40
SOURCE = "verified-source-identity"


def _config(repo: Path, *, when: str = "deferred-windows") -> None:
    path = repo / windows_verification.CONFIG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "enabled": True,
                "when": when,
                "workflow": windows_verification.DEFAULT_CALLER_WORKFLOW,
                "commands": [
                    {"name": "publish", "command": "dotnet publish App.csproj"},
                    {"name": "smoke", "command": "pwsh -File smoke.ps1"},
                ],
            }
        ),
        encoding="utf-8",
    )


def _state() -> dict[str, object]:
    return {
        "IssueNumber": 100,
        "RepoFullName": "example/repo",
        "BranchName": "autodev/issue-100",
        "LastCommitSha": HEAD,
        "ShippedSourceIdentity": SOURCE,
        "ShippedTreeVerified": True,
        "WindowsVerificationRequired": True,
        "DeferredVerificationObligations": [
            {
                "id": "windows-obligation",
                "platform": "windows",
                "message": "Windows publish is deferred",
                "source": "local-check",
            }
        ],
    }


class FakeActionsRunner:
    def __init__(self, *, conclusion: str = "success", failed_logs: str = "") -> None:
        self.conclusion = conclusion
        self.failed_logs = failed_logs
        self.calls: list[list[str]] = []
        self.run_list_count = 0

    def __call__(self, command, **kwargs):
        command = [str(value) for value in command]
        self.calls.append(command)
        if command[:2] == ["gh", "api"] and command[-1].endswith("/actions/permissions"):
            return SimpleNamespace(returncode=0, stdout='{"enabled":true}', stderr="")
        if command[:3] == ["gh", "workflow", "view"]:
            return SimpleNamespace(returncode=0, stdout="name: AutoDev Windows verification\n", stderr="")
        if command[:3] == ["gh", "run", "list"]:
            self.run_list_count += 1
            if self.run_list_count == 1:
                value = []
            else:
                value = [
                    {
                        "databaseId": 321,
                        "headSha": HEAD,
                        "status": "completed",
                        "conclusion": self.conclusion,
                        "url": "https://github.com/example/repo/actions/runs/321",
                        "createdAt": "2026-08-15T06:00:00Z",
                    }
                ]
            return SimpleNamespace(returncode=0, stdout=json.dumps(value), stderr="")
        if command[:3] == ["gh", "workflow", "run"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if command[:3] == ["gh", "run", "view"] and "--log-failed" in command:
            return SimpleNamespace(returncode=0, stdout=self.failed_logs, stderr="")
        raise AssertionError(f"unexpected command: {command}")


class WindowsVerificationTests(unittest.TestCase):
    def test_config_uses_target_workflow_not_remote_runner(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            _config(repo)
            config = windows_verification.load_config(repo)

        self.assertEqual(config["workflow"], windows_verification.DEFAULT_CALLER_WORKFLOW)
        self.assertNotIn("runner", config)
        self.assertEqual([item["name"] for item in config["commands"]], ["publish", "smoke"])

    def test_local_deferred_lines_are_durable_and_only_explicit_windows_requires_lane(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = repo / ".autodev-run" / "current"
            current.mkdir(parents=True)
            _config(repo)
            state: dict[str, object] = {}
            metadata = windows_verification.record_local_deferred_obligations(
                repo,
                current,
                state,
                "\n".join(
                    [
                        "DEFERRED: Windows-targeted test project App.Tests.csproj cannot run on Linux; verify it on Windows.",
                        "DEFERRED: iOS signing requires a compatible host.",
                    ]
                ),
            )
            persisted = json.loads((current / "deferred-verification.json").read_text(encoding="utf-8"))

        self.assertTrue(metadata["windows_verification_required"])
        self.assertEqual(
            [item["platform"] for item in metadata["deferred_verification_obligations"]],
            ["windows", "compatible-host"],
        )
        self.assertEqual(
            persisted["windows_config"]["workflow"],
            windows_verification.DEFAULT_CALLER_WORKFLOW,
        )

    def test_always_policy_creates_windows_obligation_without_fake_linux_pass(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = repo / ".autodev-run" / "current"
            current.mkdir(parents=True)
            _config(repo, when="always")
            state: dict[str, object] = {}
            windows_verification.record_local_deferred_obligations(repo, current, state, "LOCAL_CHECK_PASSED\n")

        self.assertTrue(state["WindowsVerificationRequired"])
        obligations = state["DeferredVerificationObligations"]
        self.assertEqual(len(obligations), 1)
        self.assertEqual(obligations[0]["source"], "repository-policy")
        self.assertNotIn("WindowsVerificationProof", state)

    def test_missing_default_branch_caller_workflow_is_actionable_blocker(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            _config(repo)
            config = windows_verification.load_config(repo)

            def runner(command, **kwargs):
                if command[:2] == ["gh", "api"]:
                    return SimpleNamespace(returncode=0, stdout='{"enabled":true}', stderr="")
                return SimpleNamespace(returncode=1, stdout="", stderr="workflow not found")

            with self.assertRaises(windows_verification.WindowsVerificationError) as caught:
                windows_verification.validate_actions_installation(
                    repo,
                    repo_full="example/repo",
                    config=config,
                    runner=runner,
                )

        message = str(caught.exception)
        self.assertIn("default branch", message)
        self.assertIn("Re-run the AutoDev installer", message)

    def test_windows_success_dispatches_exact_branch_sha_before_pr_and_binds_proof(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = repo / ".autodev-run" / "current"
            current.mkdir(parents=True)
            _config(repo)
            state = _state()
            runner = FakeActionsRunner()
            with mock.patch.dict(os.environ, {"AUTODEV_WINDOWS_ACTIONS_POLL_SECONDS": "0"}):
                result = windows_verification.run_after_push(
                    repo,
                    current,
                    state,
                    max_repair_attempts=3,
                    runner=runner,
                )
            proof = state["WindowsVerificationProof"]
            request = json.loads((current / windows_verification.REQUEST_FILE).read_text(encoding="utf-8"))

        self.assertEqual(result["state"], "CONTINUE")
        self.assertEqual(proof["transport"], "github-actions")
        self.assertEqual(proof["head_sha"], HEAD)
        self.assertEqual(proof["source_identity"], SOURCE)
        self.assertEqual(proof["run_id"], 321)
        self.assertEqual(request["branch"], "autodev/issue-100")
        dispatch = next(call for call in runner.calls if call[:3] == ["gh", "workflow", "run"])
        self.assertIn("--ref", dispatch)
        self.assertIn("autodev/issue-100", dispatch)
        self.assertIn(f"expected_sha={HEAD}", dispatch)
        self.assertNotIn("PrHeadSha", _state())

    def test_windows_code_failure_enters_fixer_only_after_command_started(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = repo / ".autodev-run" / "current"
            current.mkdir(parents=True)
            _config(repo)
            (current / "issue.md").write_text("# Windows journey\n", encoding="utf-8")
            runner = FakeActionsRunner(
                conclusion="failure",
                failed_logs=(
                    "Execute Windows verification AUTODEV_WINDOWS_COMMAND_START=publish\n"
                    "error CS1002: ; expected\n"
                ),
            )
            state = _state()
            with mock.patch.dict(os.environ, {"AUTODEV_WINDOWS_ACTIONS_POLL_SECONDS": "0"}):
                result = windows_verification.run_after_push(
                    repo,
                    current,
                    state,
                    max_repair_attempts=3,
                    runner=runner,
                )

        self.assertEqual(result["state"], "REPAIR")
        self.assertEqual(result["failure_classification"], windows_verification.FAILURE_CODE_REPAIRABLE)
        self.assertTrue((current / windows_verification.REPAIR_FILE).is_file())

    def test_actions_setup_failure_is_infrastructure_not_code_repair(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = repo / ".autodev-run" / "current"
            current.mkdir(parents=True)
            _config(repo)
            runner = FakeActionsRunner(
                conclusion="failure",
                failed_logs="Unable to resolve action yaron-E92/AutoDev/.github/workflows/autodev-windows-verification.yml",
            )
            state = _state()
            with mock.patch.dict(os.environ, {"AUTODEV_WINDOWS_ACTIONS_POLL_SECONDS": "0"}):
                result = windows_verification.run_after_push(
                    repo,
                    current,
                    state,
                    max_repair_attempts=3,
                    runner=runner,
                )

        self.assertEqual(result["state"], "FAILED")
        self.assertEqual(result["failure_classification"], windows_verification.FAILURE_TRANSIENT)
        self.assertFalse((current / windows_verification.REPAIR_FILE).exists())

    def test_ready_rejects_stale_or_drifted_actions_proof(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            current = Path(temp_dir)
            result_path = current / windows_verification.RESULT_FILE
            result_path.write_text(
                json.dumps(
                    {
                        "state": "passed",
                        "platform": "windows",
                        "transport": "github-actions",
                        "commit_sha": HEAD,
                        "source_identity": SOURCE,
                        "run_id": 321,
                    }
                ),
                encoding="utf-8",
            )
            proof = {
                "state": "terminal-success",
                "head_sha": HEAD,
                "source_identity": SOURCE,
                "run_id": 321,
                "result_sha256": windows_verification._sha256_file(result_path),
            }
            state = _state()
            state["PrHeadSha"] = HEAD
            state["WindowsVerificationProof"] = proof
            windows_verification.validate_ready(current, state)

            state["PrHeadSha"] = "c" * 40
            with self.assertRaises(windows_verification.WindowsVerificationError):
                windows_verification.validate_ready(current, state)

            state["PrHeadSha"] = HEAD
            result_path.write_text("{}\n", encoding="utf-8")
            with self.assertRaises(windows_verification.WindowsVerificationError):
                windows_verification.validate_ready(current, state)

    def test_opencode_resume_and_fixer_hooks_keep_windows_as_durable_repair_boundary(self):
        windows_verification.install_opencode_hooks()
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = repo / workflow_stages.CURRENT_DIR
            current.mkdir(parents=True)
            manifest_path = current / run_manifest.MANIFEST_NAME
            run_manifest.create_manifest(
                manifest_path,
                repo_path=repo,
                github_repo="example/repo",
                issue_number=100,
                mode="issue-to-pr",
                base_sha="base",
                branch="autodev/issue-100",
                role_snapshots={},
            )
            manifest = run_manifest.load_manifest(manifest_path)
            manifest["completed_stages"] = list(run_manifest.PRIMARY_STAGES)
            manifest["stages"] = {
                stage: {"status": "completed", "details": {}}
                for stage in run_manifest.PRIMARY_STAGES
            }
            run_manifest.save_manifest(manifest_path, manifest)
            state = _state()
            state["PrHeadSha"] = HEAD
            state["Status"] = "CiPassedVerifierPromptRendered"

            self.assertEqual(opencode_resume.resume_action(manifest, state), "pr-and-ci")

            result_path = current / windows_verification.RESULT_FILE
            result_path.write_text(
                json.dumps(
                    {
                        "state": "passed",
                        "platform": "windows",
                        "transport": "github-actions",
                        "commit_sha": HEAD,
                        "source_identity": SOURCE,
                        "run_id": 321,
                    }
                ),
                encoding="utf-8",
            )
            state["WindowsVerificationProof"] = {
                "state": "terminal-success",
                "head_sha": HEAD,
                "source_identity": SOURCE,
                "run_id": 321,
                "result_sha256": windows_verification._sha256_file(result_path),
            }
            manifest["completed_stages"].append(windows_verification.MANIFEST_STAGE)
            manifest["stages"][windows_verification.MANIFEST_STAGE] = {
                "status": "completed",
                "details": {"attempt": 0},
            }
            run_manifest.save_manifest(manifest_path, manifest)
            self.assertEqual(opencode_resume.resume_action(manifest, state), "ready")

            repair_path = current / windows_verification.REPAIR_FILE
            repair_path.write_text("repair windows\n", encoding="utf-8")
            self.assertEqual(opencode_adapter._fixer_source(current, "100 windows"), repair_path)

            affected = run_manifest.invalidated_stages_for_role(manifest, "implementer")
            self.assertIn(windows_verification.MANIFEST_STAGE, affected)


if __name__ == "__main__":
    unittest.main()
