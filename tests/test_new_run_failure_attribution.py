from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from automation import (
    coordination_state,
    non_success_report,
    notification_outcomes,
    operation_attribution,
    role_coordinator_contract,
    role_coordinator_stages,
    workflow_stages,
)
from automation.notification_contract import NotificationResult


class NewRunFailureAttributionTests(unittest.TestCase):
    def _prior_run(self, root: str) -> tuple[Path, Path]:
        repo = Path(root)
        current = repo / workflow_stages.CURRENT_DIR
        current.mkdir(parents=True)
        workflow_stages.write_json(
            current / "state.json",
            {
                "IssueNumber": 5,
                "Status": "ReadyForReview",
                "BranchName": "autodev/issue-5-authenticate-users",
                "LastCommitSha": "issue-5-commit",
                "PrUrl": "https://github.com/Tax-Technology/goldilocks/pull/27",
                "PrHeadSha": "issue-5-head",
                "CreatedCommitSha": "issue-5-commit",
                "CreatedTreeSha": "issue-5-tree",
                "VerifiedSourceIdentity": "issue-5-source",
                "CiProof": {"state": "terminal-success"},
            },
        )
        workflow_stages.write_json(
            current / workflow_stages.DIAGNOSTICS_FILE,
            {
                "role_invocations": {"reader": 1},
                "stage_invocations": {"ready": 1},
                "shipment_proof": {
                    "prepared_base_sha": "issue-5-base",
                    "prepared_snapshot_hash": "issue-5-snapshot",
                },
            },
        )
        (current / "run-manifest.json").write_text(
            "preserved issue-5 manifest sentinel\n",
            encoding="utf-8",
        )
        (current / non_success_report.REPORT_NAME).write_text(
            "preserved issue-5 report sentinel\n",
            encoding="utf-8",
        )
        return repo, current

    def _bytes(self, current: Path) -> dict[str, bytes]:
        return {
            name: (current / name).read_bytes()
            for name in (
                "state.json",
                workflow_stages.DIAGNOSTICS_FILE,
                "run-manifest.json",
                non_success_report.REPORT_NAME,
            )
        }

    def test_issue_5_preserved_while_prepare_failure_is_attributed_to_requested_issue_6(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, current = self._prior_run(temp_dir)
            before = self._bytes(current)

            payload = role_coordinator_stages.terminal_payload(
                repo,
                {
                    "state": "FAILED",
                    "failed_stage": "prepare",
                    "reason": "required AutoDev setting is unavailable: repository identity",
                    "failure_classification": workflow_stages.FAILURE_DETERMINISTIC,
                },
                arguments="6",
            )

            self.assertEqual(payload["issue_number"], 6)
            self.assertEqual(payload["requested_issue_number"], 6)
            self.assertFalse(payload["new_run_prepared"])
            self.assertEqual(payload["failed_stage"], "prepare")
            self.assertEqual(payload["branch"], "")
            self.assertFalse(payload["commit_exists"])
            self.assertFalse(payload["pr_exists"])
            self.assertEqual(payload["pr_url"], "")
            self.assertEqual(payload["created_commit_sha"], "")
            self.assertEqual(payload["created_tree_sha"], "")
            self.assertEqual(payload["diagnostics"]["shipment_proof"], {})

            previous = payload["existing_durable_run"]
            self.assertEqual(previous["issue_number"], 5)
            self.assertEqual(
                previous["branch"],
                "autodev/issue-5-authenticate-users",
            )
            self.assertEqual(
                previous["pr_url"],
                "https://github.com/Tax-Technology/goldilocks/pull/27",
            )
            self.assertEqual(
                previous["shipment_proof"]["prepared_base_sha"],
                "issue-5-base",
            )
            self.assertTrue(payload["existing_durable_run_preserved"])
            self.assertEqual(self._bytes(current), before)

            reported, report_path = non_success_report.update_report(repo, payload)
            report = (
                repo / ".autodev-run" / "last-operation" / non_success_report.REPORT_NAME
            ).read_text(encoding="utf-8")

            self.assertEqual(
                report_path,
                non_success_report.OPERATION_REPORT_RELATIVE,
            )
            self.assertEqual(
                reported["non_success_report"],
                non_success_report.OPERATION_REPORT_RELATIVE,
            )
            self.assertIn("Requested operation: start issue", report)
            self.assertIn("#6", report)
            self.assertIn("New run preparation completed", report)
            self.assertIn("## Existing durable run preserved", report)
            self.assertIn("#5", report)
            self.assertIn(
                "https://github.com/Tax-Technology/goldilocks/pull/27",
                report,
            )
            self.assertIn("This preserved run was not the failing operation.", report)
            self.assertIn("No issue #6 branch, commit, or PR was created.", report)
            self.assertIn("Stage:", report)
            self.assertIn("prepare", report)
            self.assertNotIn("Branch for requested issue: autodev/issue-5", report)
            self.assertEqual(self._bytes(current), before)

    def test_preflight_for_different_requested_issue_does_not_touch_prior_run_diagnostics(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, current = self._prior_run(temp_dir)
            before = (current / workflow_stages.DIAGNOSTICS_FILE).read_bytes()

            with patch("automation.workflow_dispatch._preflight"):
                code, payload = workflow_stages.execute_stage(
                    "preflight",
                    repo,
                    arguments="6",
                    runner=lambda *_args, **_kwargs: SimpleNamespace(
                        returncode=0,
                        stdout="",
                        stderr="",
                    ),
                    which=lambda name: f"/tools/{name}",
                )

            self.assertEqual(code, 0)
            self.assertEqual(payload["issue_number"], 6)
            self.assertFalse(payload["new_run_prepared"])
            self.assertEqual(
                (current / workflow_stages.DIAGNOSTICS_FILE).read_bytes(),
                before,
            )

    def test_run_stage_prepare_exception_does_not_checkpoint_prior_manifest(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, current = self._prior_run(temp_dir)
            manifest_before = (current / "run-manifest.json").read_bytes()

            with patch.object(
                workflow_stages,
                "execute_stage",
                side_effect=workflow_stages.WorkflowStageError(
                    "preparation failed before current replacement"
                ),
            ), patch.object(
                role_coordinator_stages.role_resume,
                "checkpoint_failure",
            ) as checkpoint:
                with self.assertRaises(
                    role_coordinator_contract.RoleCoordinatorError
                ):
                    role_coordinator_stages.run_stage(
                        repo,
                        "prepare",
                        runtime_name="opencode",
                        arguments="6",
                    )

            checkpoint.assert_not_called()
            self.assertEqual(
                (current / "run-manifest.json").read_bytes(),
                manifest_before,
            )

    def test_failure_after_requested_issue_is_current_uses_requested_run_metadata(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, current = self._prior_run(temp_dir)
            workflow_stages.write_json(
                current / "state.json",
                {
                    "IssueNumber": 6,
                    "Status": "Prepared",
                    "BranchName": "autodev/issue-6-new-run",
                    "LastCommitSha": "",
                    "PrUrl": "",
                    "PrHeadSha": "",
                },
            )
            (current / "run-manifest.json").unlink()

            payload = role_coordinator_stages.terminal_payload(
                repo,
                {
                    "state": "FAILED",
                    "failed_stage": "reader",
                    "reason": "reader failed after preparation",
                    "failure_classification": workflow_stages.FAILURE_DETERMINISTIC,
                },
                arguments="6",
            )

            self.assertEqual(payload["issue_number"], 6)
            self.assertEqual(payload["requested_issue_number"], 6)
            self.assertTrue(payload["new_run_prepared"])
            self.assertEqual(payload["branch"], "autodev/issue-6-new-run")
            self.assertNotIn("existing_durable_run", payload)

    def test_resume_identity_remains_durable_current_issue(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, _current = self._prior_run(temp_dir)

            self.assertEqual(coordination_state.issue_number(repo), 5)
            self.assertEqual(
                coordination_state.issue_number(repo, "6"),
                5,
                "resume/status identity remains durable-state first",
            )

    def test_notification_diagnostic_does_not_modify_preserved_prior_run(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, current = self._prior_run(temp_dir)
            before = (current / workflow_stages.DIAGNOSTICS_FILE).read_bytes()
            payload = operation_attribution.attribute_explicit_new_run(
                repo,
                {
                    "state": "FAILED",
                    "failed_stage": "prepare",
                    "failure_classification": workflow_stages.FAILURE_DETERMINISTIC,
                },
                6,
            )

            notification_outcomes._record_diagnostic(
                repo,
                payload,
                None,
                NotificationResult(
                    attempted=True,
                    delivered=False,
                    backend="native",
                    reason="test",
                ),
            )

            self.assertEqual(
                (current / workflow_stages.DIAGNOSTICS_FILE).read_bytes(),
                before,
            )


if __name__ == "__main__":
    unittest.main()
