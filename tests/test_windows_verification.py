from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

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
                "runner": ["fake-windows-runner"],
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
        "PrHeadSha": HEAD,
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


class WindowsVerificationTests(unittest.TestCase):
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
        self.assertEqual(len(metadata["deferred_verification_obligations"]), 2)
        self.assertEqual(
            [item["platform"] for item in metadata["deferred_verification_obligations"]],
            ["windows", "compatible-host"],
        )
        self.assertTrue(persisted["windows_required"])
        self.assertEqual(persisted["windows_config"]["command_names"], ["publish", "smoke"])
        self.assertNotIn("runner", persisted["windows_config"])

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
        self.assertEqual(obligations[0]["platform"], "windows")
        self.assertEqual(obligations[0]["source"], "repository-policy")
        self.assertNotIn("WindowsVerificationProof", state)

    def test_required_lane_without_config_blocks_after_ci_instead_of_marking_ready(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = repo / ".autodev-run" / "current"
            current.mkdir(parents=True)
            state = _state()
            result = windows_verification.run_after_ci(
                repo,
                current,
                state,
                max_repair_attempts=3,
            )

        self.assertIsNotNone(result)
        self.assertEqual(result["state"], "BLOCKED")
        self.assertEqual(result["failed_stage"], "windows-verification")
        self.assertEqual(result["failure_classification"], windows_verification.FAILURE_DETERMINISTIC)

    def test_windows_success_is_bound_to_exact_shipped_commit_and_source_identity(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = repo / ".autodev-run" / "current"
            current.mkdir(parents=True)
            _config(repo)
            state = _state()

            def runner(command, **kwargs):
                self.assertEqual(command, ["fake-windows-runner"])
                request = json.loads(kwargs["input"])
                self.assertEqual(request["commit_sha"], HEAD)
                self.assertEqual(request["source_identity"], SOURCE)
                self.assertEqual([item["name"] for item in request["commands"]], ["publish", "smoke"])
                return SimpleNamespace(
                    returncode=0,
                    stdout=json.dumps(
                        {
                            "version": 1,
                            "state": "passed",
                            "platform": "windows",
                            "commit_sha": HEAD,
                            "source_identity": SOURCE,
                            "commands": [
                                {"name": "publish", "returncode": 0, "output": "publish ok"},
                                {"name": "smoke", "returncode": 0, "output": "smoke ok"},
                            ],
                        }
                    ),
                    stderr="",
                )

            result = windows_verification.run_after_ci(
                repo,
                current,
                state,
                max_repair_attempts=3,
                runner=runner,
            )
            proof = state["WindowsVerificationProof"]
            request = json.loads((current / windows_verification.REQUEST_FILE).read_text(encoding="utf-8"))
            persisted_result = json.loads((current / windows_verification.RESULT_FILE).read_text(encoding="utf-8"))
            windows_verification.validate_ready(current, state)

        self.assertEqual(result["state"], "CONTINUE")
        self.assertEqual(proof["state"], "terminal-success")
        self.assertEqual(proof["head_sha"], HEAD)
        self.assertEqual(proof["source_identity"], SOURCE)
        self.assertEqual(request["commit_sha"], HEAD)
        self.assertEqual(persisted_result["platform"], "windows")

    def test_identity_mismatch_is_not_accepted_as_windows_success(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = repo / ".autodev-run" / "current"
            current.mkdir(parents=True)
            _config(repo)
            state = _state()

            def runner(command, **kwargs):
                return SimpleNamespace(
                    returncode=0,
                    stdout=json.dumps(
                        {
                            "version": 1,
                            "state": "passed",
                            "platform": "windows",
                            "commit_sha": "b" * 40,
                            "source_identity": SOURCE,
                            "commands": [],
                        }
                    ),
                    stderr="",
                )

            result = windows_verification.run_after_ci(
                repo,
                current,
                state,
                max_repair_attempts=3,
                runner=runner,
            )

        self.assertEqual(result["state"], "FAILED")
        self.assertEqual(result["failure_classification"], windows_verification.FAILURE_DETERMINISTIC)
        self.assertNotIn("WindowsVerificationProof", state)

    def test_windows_code_failure_enters_fixer_but_network_failure_does_not(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = repo / ".autodev-run" / "current"
            current.mkdir(parents=True)
            _config(repo)
            (current / "issue.md").write_text("# Windows journey\n", encoding="utf-8")

            def code_runner(command, **kwargs):
                return SimpleNamespace(
                    returncode=0,
                    stdout=json.dumps(
                        {
                            "version": 1,
                            "state": "code-failure",
                            "platform": "windows",
                            "commit_sha": HEAD,
                            "source_identity": SOURCE,
                            "commands": [
                                {"name": "publish", "returncode": 1, "output": "CS1002 compile error"}
                            ],
                        }
                    ),
                    stderr="",
                )

            code_state = _state()
            code_result = windows_verification.run_after_ci(
                repo,
                current,
                code_state,
                max_repair_attempts=3,
                runner=code_runner,
            )
            self.assertTrue((current / windows_verification.REPAIR_FILE).is_file())

            def transient_runner(command, **kwargs):
                return SimpleNamespace(
                    returncode=0,
                    stdout=json.dumps(
                        {
                            "version": 1,
                            "state": "code-failure",
                            "platform": "windows",
                            "commit_sha": HEAD,
                            "source_identity": SOURCE,
                            "commands": [
                                {
                                    "name": "publish",
                                    "returncode": 1,
                                    "output": "NU1301 Unable to load the service index; connection reset",
                                }
                            ],
                        }
                    ),
                    stderr="",
                )

            transient_state = _state()
            transient_result = windows_verification.run_after_ci(
                repo,
                current,
                transient_state,
                max_repair_attempts=3,
                runner=transient_runner,
            )

        self.assertEqual(code_result["state"], "REPAIR")
        self.assertEqual(code_result["failure_classification"], windows_verification.FAILURE_CODE_REPAIRABLE)
        self.assertEqual(transient_result["state"], "FAILED")
        self.assertEqual(transient_result["failure_classification"], windows_verification.FAILURE_TRANSIENT)

    def test_ready_rejects_stale_or_drifted_windows_proof(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            current = Path(temp_dir)
            result_path = current / windows_verification.RESULT_FILE
            result_path.write_text(
                json.dumps(
                    {
                        "state": "passed",
                        "platform": "windows",
                        "commit_sha": HEAD,
                        "source_identity": SOURCE,
                    }
                ),
                encoding="utf-8",
            )
            proof = {
                "state": "terminal-success",
                "head_sha": HEAD,
                "source_identity": SOURCE,
                "result_sha256": windows_verification._sha256_file(result_path),
            }
            state = _state()
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
            state["Status"] = "CiPassedVerifierPromptRendered"

            self.assertEqual(opencode_resume.resume_action(manifest, state), "pr-and-ci")

            result_path = current / windows_verification.RESULT_FILE
            result_path.write_text(
                json.dumps(
                    {
                        "state": "passed",
                        "platform": "windows",
                        "commit_sha": HEAD,
                        "source_identity": SOURCE,
                    }
                ),
                encoding="utf-8",
            )
            state["WindowsVerificationProof"] = {
                "state": "terminal-success",
                "head_sha": HEAD,
                "source_identity": SOURCE,
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
