from __future__ import annotations

from automation import windows_verification_storage

from automation import windows_verification_contract

from automation import opencode_resume_status

from automation import opencode_resume_checkpoint

from automation import semantic_evidence

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from automation import run_manifest, windows_semantic_order, windows_verification_execution, workflow_stages


HEAD = "a" * 40
SOURCE = "verified-source-identity"
BASE = "b" * 40


def _write_state(current: Path, state: dict[str, object]) -> None:
    current.mkdir(parents=True, exist_ok=True)
    (current / "state.json").write_text(
        json.dumps(state, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _presemantic_state() -> dict[str, object]:
    return {
        "IssueNumber": 137,
        "RepoFullName": "example/repo",
        "BranchName": "autodev/issue-137",
        "BaseSha": BASE,
        "OpenCodeProtocolVersion": 1,
        "LastLocalCheckPassed": True,
        "VerifiedParentSha": BASE,
        "VerifiedSourceIdentity": SOURCE,
        "VerifiedChanges": [
            {"path": "src/App.cs", "status": "modified", "sha256": "ABC"}
        ],
        "WindowsVerificationRequired": True,
        "DeferredVerificationObligations": [
            {
                "id": "windows-smoke",
                "platform": "windows",
                "message": "Publish and smoke-test the Windows journey.",
                "source": "local-check",
            }
        ],
        "LastSemanticVerdict": "blocked",
    }


class WindowsSemanticOrderTests(unittest.TestCase):
    def test_blocked_semantic_run_routes_to_windows_before_verifier(self):
        state = _presemantic_state()
        self.assertTrue(windows_semantic_order._needs_presemantic_windows(state))

        state["LastSemanticVerdict"] = "pass"
        self.assertFalse(windows_semantic_order._needs_presemantic_windows(state))

        state["LastSemanticVerdict"] = "blocked"
        state["WindowsVerificationRequired"] = False
        self.assertFalse(windows_semantic_order._needs_presemantic_windows(state))

    def test_presemantic_windows_success_commits_exact_source_but_does_not_create_pr(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = repo / workflow_stages.CURRENT_DIR
            state = _presemantic_state()
            _write_state(current, state)

            def create_commit(repo, state, changes, current, *, runner):
                persisted = workflow_stages.read_state(current)
                persisted.update(
                    {
                        "CreatedCommitSha": HEAD,
                        "CreatedTreeSha": "tree-sha",
                        "CreatedParentSha": BASE,
                        "ShippedSourceIdentity": SOURCE,
                        "ShippedTreeVerified": True,
                    }
                )
                workflow_stages.write_state(current, persisted)
                return HEAD

            def run_windows(repo, current, state, *, max_repair_attempts, runner):
                proof = {
                    "state": "terminal-success",
                    "head_sha": HEAD,
                    "source_identity": SOURCE,
                    "run_id": 321,
                    "run_url": "https://github.com/example/repo/actions/runs/321",
                }
                persisted = workflow_stages.read_state(current)
                persisted["WindowsVerificationProof"] = proof
                persisted["Status"] = "WindowsVerificationPassed"
                workflow_stages.write_state(current, persisted)
                result_path = current / windows_verification_contract.RESULT_FILE
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
                return {
                    "state": "CONTINUE",
                    "failed_stage": "",
                    "failure_classification": "",
                    "artifact": str(result_path),
                    "platform_verification_stage": windows_verification_contract.MANIFEST_STAGE,
                    "windows_repair_attempt": 0,
                    "windows_verification_proof": proof,
                    "windows_stage_completed": True,
                }

            with (
                mock.patch.object(
                    workflow_stages,
                    "workspace_changes",
                    return_value=[{"Path": "src/App.cs", "Status": "modified"}],
                ),
                mock.patch.object(workflow_stages, "create_api_commit", side_effect=create_commit),
                mock.patch.object(windows_verification_execution, "run_after_push", side_effect=run_windows),
            ):
                payload = windows_semantic_order._run_presemantic_windows(
                    repo,
                    current,
                    state,
                    attempt=0,
                    runner=lambda *args, **kwargs: None,
                )

            persisted = workflow_stages.read_state(current)

        self.assertEqual(payload["state"], "CONTINUE")
        self.assertTrue(payload["platform_verification_only"])
        self.assertEqual(payload["next_action"], "run semantic verification with the current Windows proof")
        self.assertEqual(persisted["LastCommitSha"], HEAD)
        self.assertFalse(bool(persisted.get("PrUrl")))
        self.assertTrue(bool(persisted.get("WindowsVerificationProof")))

    def test_presemantic_windows_code_failure_stays_in_windows_repair_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = repo / workflow_stages.CURRENT_DIR
            state = _presemantic_state()
            state.update(
                {
                    "LastCommitSha": HEAD,
                    "CreatedCommitSha": HEAD,
                    "CreatedParentSha": BASE,
                    "ShippedSourceIdentity": SOURCE,
                    "ShippedTreeVerified": True,
                }
            )
            _write_state(current, state)
            repair = {
                "state": "REPAIR",
                "failed_stage": "windows-verification",
                "failure_classification": windows_verification_contract.FAILURE_CODE_REPAIRABLE,
                "next_action": "delegate the Windows repair to autodev-fixer",
                "artifact": str(current / windows_verification_contract.REPAIR_FILE),
                "platform_verification_stage": windows_verification_contract.MANIFEST_STAGE,
                "windows_repair_attempt": 0,
            }
            with (
                mock.patch.object(workflow_stages, "workspace_changes", return_value=[]),
                mock.patch.object(windows_verification_execution, "run_after_push", return_value=repair),
            ):
                payload = windows_semantic_order._run_presemantic_windows(
                    repo,
                    current,
                    state,
                    attempt=0,
                    runner=lambda *args, **kwargs: None,
                )

        self.assertEqual(payload, repair)
        self.assertNotIn("platform_verification_only", payload)

    def test_pushed_tree_preserves_original_local_verification_identity(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = repo / workflow_stages.CURRENT_DIR
            state = _presemantic_state()
            state.update(
                {
                    "LastCommitSha": HEAD,
                    "CreatedCommitSha": HEAD,
                    "CreatedParentSha": BASE,
                    "ShippedSourceIdentity": SOURCE,
                    "ShippedTreeVerified": True,
                }
            )
            with mock.patch.object(workflow_stages, "workspace_changes", return_value=[]):
                proof = windows_semantic_order._preserved_shipped_source_identity(
                    repo,
                    current,
                    state,
                )
            with mock.patch.object(
                workflow_stages,
                "workspace_changes",
                return_value=[{"Path": "src/App.cs", "Status": "modified"}],
            ):
                drifted = windows_semantic_order._preserved_shipped_source_identity(
                    repo,
                    current,
                    state,
                )

        self.assertEqual(proof["identity"], SOURCE)
        self.assertEqual(proof["parent_sha"], BASE)
        self.assertEqual(proof["changes"], state["VerifiedChanges"])
        self.assertIsNone(drifted)

    def test_windows_request_and_result_are_included_in_semantic_evidence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            current = Path(temp_dir)
            (current / "deferred-verification.json").write_text(
                '{"windows_required":true}\n',
                encoding="utf-8",
            )
            (current / windows_verification_contract.REQUEST_FILE).write_text(
                '{"commands":[{"name":"smoke","command":"pwsh -File smoke.ps1"}]}\n',
                encoding="utf-8",
            )
            (current / windows_verification_contract.RESULT_FILE).write_text(
                '{"state":"passed","commit_sha":"abc","source_identity":"source"}\n',
                encoding="utf-8",
            )
            evidence = windows_semantic_order._with_windows_evidence(current, "BASE EVIDENCE")

        self.assertIn("BASE EVIDENCE", evidence)
        self.assertIn("deferred-verification.json", evidence)
        self.assertIn(windows_verification_contract.REQUEST_FILE, evidence)
        self.assertIn("pwsh -File smoke.ps1", evidence)
        self.assertIn(windows_verification_contract.RESULT_FILE, evidence)
        self.assertIn('"state":"passed"', evidence)

    def test_platform_only_checkpoint_completes_windows_not_pr(self):
        windows_semantic_order.install()
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = repo / workflow_stages.CURRENT_DIR
            current.mkdir(parents=True)
            manifest_path = current / run_manifest.MANIFEST_NAME
            run_manifest.create_manifest(
                manifest_path,
                repo_path=repo,
                github_repo="example/repo",
                issue_number=137,
                mode="issue-to-pr",
                base_sha=BASE,
                branch="autodev/issue-137",
                role_snapshots={},
            )
            result_path = current / windows_verification_contract.RESULT_FILE
            result_path.write_text('{"state":"passed"}\n', encoding="utf-8")
            proof = {
                "state": "terminal-success",
                "head_sha": HEAD,
                "source_identity": SOURCE,
                "run_id": 321,
                "run_url": "https://github.com/example/repo/actions/runs/321",
                "result_sha256": windows_verification_storage._sha256_file(result_path),
            }
            state = _presemantic_state()
            state.update(
                {
                    "LastCommitSha": HEAD,
                    "CreatedCommitSha": HEAD,
                    "CreatedParentSha": BASE,
                    "ShippedSourceIdentity": SOURCE,
                    "ShippedTreeVerified": True,
                    "WindowsVerificationProof": proof,
                }
            )
            _write_state(current, state)
            payload = {
                "state": "CONTINUE",
                "failed_stage": "",
                "failure_classification": "",
                "platform_verification_only": True,
                "platform_verification_stage": windows_verification_contract.MANIFEST_STAGE,
                "windows_stage_completed": True,
                "windows_repair_attempt": 0,
                "windows_verification_proof": proof,
            }

            opencode_resume_checkpoint.checkpoint_stage(repo, "pr-and-ci", payload, 0)
            manifest = run_manifest.load_manifest(manifest_path)

        self.assertTrue(run_manifest.stage_completed(manifest, windows_verification_contract.MANIFEST_STAGE))
        self.assertFalse(run_manifest.stage_completed(manifest, "pr-created"))

    def test_resume_selects_windows_stage_before_semantic_and_then_verifier(self):
        windows_semantic_order.install()
        manifest = {
            "target": {"mode": "issue-to-pr"},
            "completed_stages": [
                "issue-selected",
                "repository-read",
                "handoff-synthesized",
                "plan-created",
                "implementation-generated",
                "patch-applied",
                "deterministic-verified",
            ],
            "stages": {},
        }
        state = _presemantic_state()

        self.assertEqual(opencode_resume_status.resume_action(manifest, state), "pr-and-ci")

        state["LastCommitSha"] = HEAD
        state["ShippedSourceIdentity"] = SOURCE
        state["WindowsVerificationProof"] = {
            "state": "terminal-success",
            "head_sha": HEAD,
            "source_identity": SOURCE,
        }
        self.assertEqual(opencode_resume_status.resume_action(manifest, state), "verifier")

        state["WindowsVerificationRequired"] = False
        state.pop("WindowsVerificationProof", None)
        self.assertEqual(opencode_resume_status.resume_action(manifest, state), "verifier")

    def test_install_augments_opencode_verifier_evidence(self):
        windows_semantic_order.install()
        with tempfile.TemporaryDirectory() as temp_dir:
            current = Path(temp_dir)
            (current / windows_verification_contract.RESULT_FILE).write_text(
                '{"state":"passed","run_id":321}\n',
                encoding="utf-8",
            )
            evidence = semantic_evidence.collect_deterministic_evidence(current)

        self.assertIn(windows_verification_contract.RESULT_FILE, evidence)
        self.assertIn('"run_id":321', evidence)


if __name__ == "__main__":
    unittest.main()
