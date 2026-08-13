from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from automation import ci_outcomes, workflow_stages


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
            original_ci_state = workflow_stages._ci_state
            original_validate_ready = workflow_stages.validate_ready_proof
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
                workflow_stages._ci_state = original_ci_state
                workflow_stages.validate_ready_proof = original_validate_ready


if __name__ == "__main__":
    unittest.main()
