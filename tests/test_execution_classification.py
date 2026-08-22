from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from automation import execution_classification as execution


class ExecutionClassificationTests(unittest.TestCase):
    def _reader_block(self, payload: dict[str, object]) -> str:
        return (
            "Repository findings.\n\n"
            + execution.CLASSIFICATION_BLOCK_START
            + "\n"
            + json.dumps(payload)
            + "\n"
            + execution.CLASSIFICATION_BLOCK_END
            + "\n"
        )

    def test_explicit_manual_issue_is_attention_required_without_fake_code_work(self):
        issue = """
# Provision external publisher identity
<!-- autodev:execution=manual-external -->

Complete third-party identity validation and provision the signing authority.
"""
        report = execution.explicit_classification(issue)

        self.assertIsNotNone(report)
        assert report is not None
        self.assertEqual(report.classification, execution.MANUAL_EXTERNAL)
        self.assertTrue(report.attention_required)
        self.assertEqual(report.autonomous_criteria, ())
        self.assertTrue(report.human_actions)
        self.assertFalse(report.partial_autonomous_execution)

    def test_events_176_style_identity_and_certificate_provisioning_is_manual_external(self):
        issue = """
# Provision the initial OV Windows publisher identity for public releases

Acceptance criteria require selecting a publicly trusted OV code-signing provider,
completing publisher identity validation, provisioning compliant hardware/cloud-HSM
custody, and establishing protected authorization. Repository documentation alone
does not provide the certificate or identity validation.
"""
        reader = self._reader_block(
            {
                "classification": "manual-external",
                "reason": "The substantive outcome requires third-party publisher identity validation and certificate/HSM provisioning outside repository tools.",
                "autonomous_criteria": [],
                "manual_criteria": [
                    "Complete publisher identity validation.",
                    "Provision the approved signing authority.",
                ],
                "human_actions": [
                    "Select the provider through the authorized purchasing/identity workflow.",
                    "Complete identity validation and provision signing authority.",
                ],
                "resume_evidence": [
                    "Record only the configured provider/account/profile identifiers and non-secret production-signing proof metadata."
                ],
                "manual_prerequisite_blocks_implementation": True,
                "autonomous_subset_independent": False,
            }
        )

        report = execution.resolve_reader_classification(reader, issue)

        self.assertEqual(report.classification, execution.MANUAL_EXTERNAL)
        self.assertTrue(report.attention_required)
        self.assertIn("identity validation", report.reason)
        self.assertEqual(len(report.manual_criteria), 2)

    def test_documentation_about_manual_work_is_not_completion_evidence(self):
        issue = """
# External setup

The README now documents how the operator will buy the service and provision the
credential later. The provider has not been provisioned.
"""
        reader = self._reader_block(
            {
                "classification": "manual-external",
                "reason": "The external account/resource still does not exist.",
                "autonomous_criteria": [],
                "manual_criteria": ["Provision the external resource."],
                "human_actions": ["Provision it through the authorized provider workflow."],
                "resume_evidence": ["Record the non-secret resource identifier after provisioning."],
                "manual_prerequisite_blocks_implementation": True,
                "autonomous_subset_independent": False,
            }
        )

        report = execution.resolve_reader_classification(reader, issue)

        self.assertFalse(report.completion_evidence_present)
        self.assertTrue(report.attention_required)

    def test_mixed_blocking_prerequisite_stops_before_implementation(self):
        issue = "# Configure provider-backed release\n"
        report = execution.parse_reader_classification(
            self._reader_block(
                {
                    "classification": "mixed",
                    "reason": "Repository wiring exists, but configuration needs an external signer identifier that has not been provisioned.",
                    "autonomous_criteria": ["Add deterministic signer-policy validation."],
                    "manual_criteria": ["Provision the signer and obtain its public identifier."],
                    "human_actions": ["Provision the signer through the provider console."],
                    "resume_evidence": ["Record the non-secret signer profile identifier."],
                    "manual_prerequisite_blocks_implementation": True,
                    "autonomous_subset_independent": False,
                }
            ),
            issue,
        )

        self.assertEqual(report.classification, execution.MIXED)
        self.assertTrue(report.attention_required)
        self.assertFalse(report.decomposition_recommended)

    def test_mixed_independent_subset_recommends_decomposition_without_narrowing_parent(self):
        issue = "# Provider setup plus independent repository cleanup\n"
        report = execution.parse_reader_classification(
            self._reader_block(
                {
                    "classification": "mixed",
                    "reason": "A repository-only cleanup is independent, while provider enrollment remains manual.",
                    "autonomous_criteria": ["Refactor release-path diagnostics."],
                    "manual_criteria": ["Complete provider enrollment."],
                    "human_actions": ["Complete provider enrollment outside AutoDev."],
                    "resume_evidence": ["Record the non-secret enrollment/profile identifier."],
                    "manual_prerequisite_blocks_implementation": False,
                    "autonomous_subset_independent": True,
                }
            ),
            issue,
        )

        self.assertTrue(report.attention_required)
        self.assertTrue(report.decomposition_recommended)
        self.assertFalse(report.partial_autonomous_execution)
        plan = execution.render_manual_action_plan(report)
        self.assertIn("child or follow-up issue", plan)
        self.assertIn("parent remains attention-required", plan)

    def test_manual_completion_marker_allows_reader_to_reclassify_remaining_work(self):
        issue = f"""
# Continue after external provisioning
<!-- autodev:execution=manual-external -->
{execution.MANUAL_EVIDENCE_MARKER}
"""
        reader = self._reader_block(
            {
                "classification": "automatable",
                "reason": "The declared external prerequisite is complete; remaining acceptance criteria are repository changes.",
                "autonomous_criteria": ["Wire the configured non-secret resource identifier."],
                "manual_criteria": [],
                "human_actions": [],
                "resume_evidence": [],
                "manual_prerequisite_blocks_implementation": False,
                "autonomous_subset_independent": False,
            }
        )

        report = execution.resolve_reader_classification(reader, issue)

        self.assertEqual(report.classification, execution.AUTOMATABLE)
        self.assertEqual(report.source, "reader-after-manual-evidence")
        self.assertTrue(report.completion_evidence_present)
        self.assertFalse(report.attention_required)

    def test_secret_values_cannot_be_requested_as_resume_evidence(self):
        issue = "# Manual setup\n"
        reader = self._reader_block(
            {
                "classification": "manual-external",
                "reason": "External setup is required.",
                "autonomous_criteria": [],
                "manual_criteria": ["Provision external service."],
                "human_actions": ["Provision the service."],
                "resume_evidence": ["Paste the API token into the issue comment."],
                "manual_prerequisite_blocks_implementation": True,
                "autonomous_subset_independent": False,
            }
        )

        with self.assertRaises(execution.ExecutionClassificationError) as caught:
            execution.parse_reader_classification(reader, issue)

        self.assertIn("secret-free", str(caught.exception))

    def test_automatable_reader_result_preserves_normal_flow(self):
        issue = "# Refactor pure Python parser\n"
        report = execution.parse_reader_classification(
            self._reader_block(
                {
                    "classification": "automatable",
                    "reason": "All criteria are repository-local code and tests.",
                    "autonomous_criteria": ["Refactor parser and update unit tests."],
                    "manual_criteria": [],
                    "human_actions": [],
                    "resume_evidence": [],
                    "manual_prerequisite_blocks_implementation": False,
                    "autonomous_subset_independent": False,
                }
            ),
            issue,
        )

        self.assertFalse(report.attention_required)
        self.assertEqual(execution.scoped_issue_text(issue, report), issue)

    def test_manual_action_plan_and_json_artifact_are_durable_and_secret_free(self):
        report = execution.ExecutionReport(
            classification=execution.MANUAL_EXTERNAL,
            reason="External identity approval is required.",
            manual_criteria=("Complete external identity approval.",),
            human_actions=("Complete the provider identity workflow.",),
            resume_evidence=("Record the non-secret approved profile identifier.",),
            manual_prerequisite_blocks_implementation=True,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            current = Path(temp_dir)
            classification_path, plan_path = execution.persist_artifacts(current, report)

            self.assertTrue(classification_path.is_file())
            self.assertIsNotNone(plan_path)
            assert plan_path is not None
            plan = plan_path.read_text(encoding="utf-8")
            self.assertIn("External identity approval", plan)
            self.assertIn("Never copy passwords, tokens, credentials", plan)
            payload = json.loads(classification_path.read_text(encoding="utf-8"))
            self.assertTrue(payload["attention_required"])
            self.assertFalse(payload["partial_autonomous_execution"])


if __name__ == "__main__":
    unittest.main()
