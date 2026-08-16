from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from automation import ci_outcomes, opencode_coordinator, workflow_stages


REAL_SUCCESS_CHECKS = [
    {"bucket": "skipping", "name": "tagAndRelease", "state": "SKIPPED"},
    {"bucket": "skipping", "name": "Test Results", "state": "NEUTRAL"},
    {"bucket": "pass", "name": "SonarCloud Code Analysis", "state": "SUCCESS"},
    {"bucket": "pass", "name": "buildAndTestGUI", "state": "SUCCESS"},
    {"bucket": "pass", "name": "buildAndTestWithoutGUI", "state": "SUCCESS"},
    {"bucket": "pass", "name": "checkWhetherGUIBuildNeeded", "state": "SUCCESS"},
    {"bucket": "pass", "name": "gitVersion", "state": "SUCCESS"},
]


class CiOutcomeTests(unittest.TestCase):
    def _install_originals(self) -> dict[str, object]:
        return {
            "ci_state": workflow_stages._ci_state,
            "validate_ready": workflow_stages.validate_ready_proof,
            "wait_for_checks": workflow_stages.wait_for_required_checks,
            "pr_and_ci": workflow_stages.pr_and_ci,
            "execute_stage": workflow_stages.execute_stage,
            "coordinator_run_stage": opencode_coordinator.run_stage,
            "coordinator_coordinate": opencode_coordinator.coordinate,
        }

    def _restore_install_originals(self, originals: dict[str, object]) -> None:
        workflow_stages._ci_state = originals["ci_state"]  # type: ignore[assignment]
        workflow_stages.validate_ready_proof = originals["validate_ready"]  # type: ignore[assignment]
        workflow_stages.wait_for_required_checks = originals["wait_for_checks"]  # type: ignore[assignment]
        workflow_stages.pr_and_ci = originals["pr_and_ci"]  # type: ignore[assignment]
        workflow_stages.execute_stage = originals["execute_stage"]  # type: ignore[assignment]
        opencode_coordinator.run_stage = originals["coordinator_run_stage"]  # type: ignore[assignment]
        opencode_coordinator.coordinate = originals["coordinator_coordinate"]  # type: ignore[assignment]

    def test_real_pass_skipping_neutral_set_is_terminal_success(self):
        self.assertEqual(ci_outcomes.ci_state(REAL_SUCCESS_CHECKS), "terminal-success")

    def test_pass_only_is_terminal_success(self):
        checks = [{"bucket": "pass", "name": "build", "state": "SUCCESS"}]
        self.assertEqual(ci_outcomes.ci_state(checks), "terminal-success")

    def test_explicit_failure_remains_terminal_failure(self):
        checks = [
            {"bucket": "pass", "name": "build", "state": "SUCCESS"},
            {"bucket": "fail", "name": "test", "state": "FAILURE"},
            {"bucket": "skipping", "name": "release", "state": "SKIPPED"},
        ]
        self.assertEqual(ci_outcomes.ci_state(checks), "terminal-failure")

    def test_pending_remains_in_progress(self):
        checks = [
            {"bucket": "pass", "name": "build", "state": "SUCCESS"},
            {"bucket": "pending", "name": "test", "state": "IN_PROGRESS"},
        ]
        self.assertEqual(ci_outcomes.ci_state(checks), "queued/in-progress")

    def test_empty_checks_are_never_success(self):
        self.assertEqual(ci_outcomes.ci_state([]), "not-observed")

    def test_pending_ci_after_poll_budget_becomes_waiting_without_repair(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = repo / ".autodev-run" / "current"
            current.mkdir(parents=True)
            workflow_stages.write_json(
                current / "state.json",
                {
                    "IssueNumber": 126,
                    "BranchName": "autodev/issue-126",
                    "LastCommitSha": "head-sha",
                    "PrHeadSha": "head-sha",
                    "PrUrl": "https://example.test/pr/126",
                    "CiProof": {
                        "head_sha": "head-sha",
                        "state": "queued/in-progress",
                        "checks": [
                            {"bucket": "pending", "name": "buildAndTestGUI", "state": "IN_PROGRESS"}
                        ],
                        "polls": 12,
                        "required_only": True,
                    },
                },
            )

            originals = self._install_originals()

            def pending_pr_and_ci(*args, **kwargs):
                raise workflow_stages.WorkflowStageError(
                    "required CI did not reach terminal state",
                    classification=workflow_stages.FAILURE_TRANSIENT,
                )

            try:
                workflow_stages.pr_and_ci = pending_pr_and_ci
                ci_outcomes.install()
                code, payload = workflow_stages.execute_stage(
                    "pr-and-ci",
                    repo,
                    autodev_root=repo,
                )
            finally:
                self._restore_install_originals(originals)

            self.assertEqual(code, 0)
            self.assertEqual(payload["state"], "WAITING")
            self.assertEqual(payload["waiting_reason"], "ci-pending")
            self.assertEqual(payload["pr_head_sha"], "head-sha")
            self.assertEqual(payload["ci_polls"], 12)
            self.assertIn("coordinate --resume", str(payload["next_action"]))
            self.assertFalse((current / "ci-repair.md").exists())

    def test_waiting_stage_short_circuits_the_python_coordinator(self):
        originals = self._install_originals()
        try:
            ci_outcomes.install()
            with patch(
                "automation.opencode_adapter.workflow_stage",
                return_value=(
                    0,
                    {
                        "state": "WAITING",
                        "completed_stage": "pr-and-ci",
                        "waiting_reason": "ci-pending",
                    },
                ),
            ):
                with self.assertRaises(ci_outcomes._CiWaiting):
                    opencode_coordinator.run_stage(Path("."), "pr-and-ci")
        finally:
            self._restore_install_originals(originals)

    def test_ready_proof_accepts_same_non_failing_semantics(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = repo / ".autodev-run" / "current"
            current.mkdir(parents=True)
            state = {
                "VerificationProofVersion": 1,
                "OpenCodeProtocolVersion": 1,
                "RepoFullName": "owner/repo",
                "LastCommitSha": "commit",
                "CreatedCommitSha": "commit",
                "CreatedTreeSha": "tree",
                "CreatedParentSha": "parent",
                "VerifiedParentSha": "parent",
                "VerifiedSourceIdentity": "identity",
                "ShippedSourceIdentity": "identity",
                "ShippedTreeVerified": True,
                "LastLocalCheckPassed": True,
                "LastSemanticVerdict": "pass",
                "SemanticSourceIdentity": "identity",
                "PrUrl": "https://example.test/pr/1",
                "PrNumber": 1,
                "CiProof": {
                    "head_sha": "commit",
                    "state": "terminal-success",
                    "checks": REAL_SUCCESS_CHECKS,
                },
            }
            originals = self._install_originals()
            try:
                ci_outcomes.install()
                with (
                    patch("automation.workflow_stages._pr_head_sha", return_value="commit"),
                    patch(
                        "automation.workflow_stages.gh_json",
                        return_value={"tree": {"sha": "tree"}, "parents": [{"sha": "parent"}]},
                    ),
                ):
                    workflow_stages.validate_ready_proof(current, state)
            finally:
                self._restore_install_originals(originals)


if __name__ == "__main__":
    unittest.main()
