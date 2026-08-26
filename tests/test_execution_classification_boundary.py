from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from automation import execution_classification as execution
from automation import execution_classification_boundary as boundary
from automation import execution_classification_hooks
from automation import opencode_adapter_handoff, opencode_adapter_roles, workflow_stages
from automation.opencode_adapter_contract import OpenCodeAdapterError


class ExecutionClassificationBoundaryTests(unittest.TestCase):
    def _block(self, payload: dict[str, object]) -> str:
        return (
            "Reader findings.\n\n"
            + execution.CLASSIFICATION_BLOCK_START
            + "\n"
            + json.dumps(payload)
            + "\n"
            + execution.CLASSIFICATION_BLOCK_END
            + "\n"
        )

    def _manual_payload(
        self,
        criterion: str,
        human_action: str,
        *,
        boundaries: list[dict[str, object]] | None = None,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "classification": "manual-external",
            "reason": "Reader claims a manual prerequisite exists.",
            "autonomous_criteria": [],
            "manual_criteria": [criterion],
            "human_actions": [human_action],
            "resume_evidence": ["Record only non-secret completion metadata."],
            "manual_prerequisite_blocks_implementation": True,
            "autonomous_subset_independent": False,
        }
        if boundaries is not None:
            payload[boundary.EXTERNAL_BOUNDARY_FIELD] = boundaries
        return payload

    def _forged_boundary(self, criterion: str, human_action: str) -> dict[str, object]:
        return {
            "criterion": criterion,
            "boundary_kind": "unsupported-external-capability",
            "human_action": human_action,
            "external_system": "developer workstation",
            "unavailable_state": human_action,
            "why_unsupported": "Reader claims a human must do this work.",
        }

    def test_repository_implementation_cannot_be_relabelled_as_external_even_with_forged_evidence(self):
        cases = (
            (
                "Implement .NET group controllers and API endpoints.",
                "Write group API code.",
            ),
            (
                "Create the required EF Core migrations.",
                "Run EF Core migrations locally.",
            ),
            (
                "Implement repository permission logic.",
                "Add authorization logic and tests.",
            ),
            (
                "Update frontend API integration.",
                "Update TypeScript types and schema bindings.",
            ),
        )
        for criterion, human_action in cases:
            with self.subTest(human_action=human_action):
                payload = self._manual_payload(
                    criterion,
                    human_action,
                    boundaries=[self._forged_boundary(criterion, human_action)],
                )
                with self.assertRaises(boundary.ExternalBoundaryEvidenceError) as caught:
                    boundary.validate_reader_external_boundary(self._block(payload))

                self.assertTrue(
                    "repository" in str(caught.exception).casefold()
                    or "migration" in str(caught.exception).casefold()
                    or "supported" in str(caught.exception).casefold()
                )

    def test_goldilocks_issue_6_observed_false_positive_requires_real_external_evidence(self):
        payload = {
            "classification": "manual-external",
            "reason": "Missing core API endpoints, migrations, and repo-level permission logic.",
            "autonomous_criteria": [],
            "manual_criteria": [
                "implement .NET group controllers, migration scripts, frontend API integration"
            ],
            "human_actions": [
                "write group API code",
                "run migrations locally",
                "update TypeScript types to match schema",
            ],
            "resume_evidence": ["Record completion in non-secret metadata."],
            "manual_prerequisite_blocks_implementation": True,
            "autonomous_subset_independent": False,
        }

        with self.assertRaises(boundary.ExternalBoundaryEvidenceError) as caught:
            boundary.validate_reader_external_boundary(self._block(payload))

        self.assertIn("external_boundaries", str(caught.exception))

    def test_genuine_certificate_and_identity_boundary_remains_accepted(self):
        criterion = "Complete publisher identity validation and certificate issuance."
        human_action = "Complete legal identity validation with the public code-signing provider."
        payload = self._manual_payload(
            criterion,
            human_action,
            boundaries=[
                {
                    "criterion": criterion,
                    "boundary_kind": "human-legal-provider-approval",
                    "human_action": human_action,
                    "external_system": "public code-signing certificate authority",
                    "unavailable_state": "publisher identity approval and an issued production signing certificate are not yet available",
                    "why_unsupported": "the provider requires legal publisher identity verification that repository and GitHub tooling cannot perform",
                }
            ],
        )

        evidence = boundary.validate_reader_external_boundary(self._block(payload))

        self.assertEqual(len(evidence), 1)
        self.assertEqual(
            evidence[0].boundary_kind,
            "human-legal-provider-approval",
        )

    def test_blocking_mixed_unavailable_provider_identifier_remains_accepted(self):
        criterion = "Provision the production signer and obtain its public profile identifier."
        human_action = "Provision the signer in the authorized provider account."
        payload = {
            "classification": "mixed",
            "reason": "Repository wiring depends on a signer profile that does not exist yet.",
            "autonomous_criteria": ["Validate signer configuration once an identifier is available."],
            "manual_criteria": [criterion],
            "human_actions": [human_action],
            "resume_evidence": ["Record the non-secret signer profile identifier."],
            "manual_prerequisite_blocks_implementation": True,
            "autonomous_subset_independent": False,
            "external_boundaries": [
                {
                    "criterion": criterion,
                    "boundary_kind": "unavailable-external-resource",
                    "human_action": human_action,
                    "external_system": "production signing provider",
                    "unavailable_state": "no production signer profile or public profile identifier has been provisioned",
                    "why_unsupported": "the provider account must provision the resource before repository tooling can reference its identifier",
                }
            ],
        }

        evidence = boundary.validate_reader_external_boundary(self._block(payload))
        report = execution.resolve_reader_classification(
            self._block(payload),
            "# Configure provider-backed release\n",
        )

        self.assertEqual(len(evidence), 1)
        self.assertEqual(report.classification, execution.MIXED)
        self.assertTrue(report.attention_required)

    def test_explicit_automatable_operator_marker_only_allows_evidence_backed_safety_downgrade(self):
        issue = """
# Implement repository feature
<!-- autodev:execution=automatable -->
"""
        repository_criterion = "Implement missing API endpoints and permission logic."
        repository_action = "Write API code and tests."
        false_reader = self._block(
            self._manual_payload(repository_criterion, repository_action)
        )

        with self.assertRaises(boundary.ExternalBoundaryEvidenceError):
            boundary.validate_reader_external_boundary(false_reader)

        external_criterion = "Complete publisher identity validation."
        external_action = "Complete identity approval with the certificate provider."
        genuine_reader = self._block(
            self._manual_payload(
                external_criterion,
                external_action,
                boundaries=[
                    {
                        "criterion": external_criterion,
                        "boundary_kind": "human-legal-provider-approval",
                        "human_action": external_action,
                        "external_system": "certificate provider",
                        "unavailable_state": "publisher identity is not approved",
                        "why_unsupported": "provider identity approval requires an authorized human/legal workflow",
                    }
                ],
            )
        )
        boundary.validate_reader_external_boundary(genuine_reader)
        report = execution.resolve_reader_classification(genuine_reader, issue)

        self.assertEqual(report.classification, execution.MANUAL_EXTERNAL)
        self.assertEqual(report.source, "reader-safety-downgrade")
        self.assertTrue(report.attention_required)

    def _write_protocol_state(self, repo: Path) -> Path:
        current = repo / workflow_stages.CURRENT_DIR
        current.mkdir(parents=True)
        issue_text = "# Issue #6\n\nCreate and manage persistent decision spaces.\n"
        state: dict[str, object] = {
            "Status": "Prepared",
            "IssueNumber": 6,
            "IssueText": issue_text,
            "ProviderProfile": "",
        }
        execution.enable_protocol(state)
        workflow_stages.write_state(current, state)
        (current / "issue.md").write_text(issue_text, encoding="utf-8")
        return current

    def _automatable_block(self) -> str:
        return self._block(
            {
                "classification": "automatable",
                "reason": "All remaining work is repository code, migrations, integration, and tests.",
                "autonomous_criteria": ["Implement and verify the requested repository changes."],
                "manual_criteria": [],
                "human_actions": [],
                "resume_evidence": [],
                "manual_prerequisite_blocks_implementation": False,
                "autonomous_subset_independent": False,
                "external_boundaries": [],
            }
        )

    def test_false_manual_reader_gets_one_correction_then_normal_flow_remains_runnable(self):
        original_prepare = opencode_adapter_handoff._prepare_reader  # type: ignore[attr-defined]
        original_accept = opencode_adapter_roles._accept_role_once  # type: ignore[attr-defined]
        original_correction = opencode_adapter_roles._reader_correction_contract  # type: ignore[attr-defined]
        try:
            execution_classification_hooks._install_reader_gate()
            boundary.install()
            with tempfile.TemporaryDirectory() as temp_dir:
                repo = Path(temp_dir)
                current = self._write_protocol_state(repo)
                bad = current / "reader-brief.md"
                bad.write_text(
                    self._block(
                        {
                            "classification": "manual-external",
                            "reason": "Missing core API endpoints, migrations, and repo-level permission logic.",
                            "autonomous_criteria": [],
                            "manual_criteria": [
                                "implement .NET group controllers, migration scripts, frontend API integration"
                            ],
                            "human_actions": [
                                "write group API code",
                                "run migrations locally",
                                "update TypeScript types to match schema",
                            ],
                            "resume_evidence": ["Record completion in non-secret metadata."],
                            "manual_prerequisite_blocks_implementation": True,
                            "autonomous_subset_independent": False,
                        }
                    ),
                    encoding="utf-8",
                )

                with self.assertRaises(OpenCodeAdapterError) as first_failure:
                    opencode_adapter_roles.accept_role("reader", repo, bad)

                self.assertIn("one correction is allowed", str(first_failure.exception))
                correction = (current / "contract-correction-reader.md").read_text(
                    encoding="utf-8"
                )
                self.assertIn("unsupported-external-boundary evidence extension", correction)
                self.assertIn("Repository implementation is NOT an external boundary", correction)
                state_after_rejection = workflow_stages.read_state(current)
                self.assertEqual(state_after_rejection["Status"], "Prepared")
                self.assertNotEqual(state_after_rejection.get("QueueState"), "attention")
                self.assertFalse((current / execution.CLASSIFICATION_FILE).exists())
                self.assertFalse((current / execution.MANUAL_ACTION_PLAN_FILE).exists())

                bad.write_text(self._automatable_block(), encoding="utf-8")
                outputs = opencode_adapter_roles.accept_role("reader", repo, bad)

                state = workflow_stages.read_state(current)
                self.assertEqual(state["ExecutionClassification"], execution.AUTOMATABLE)
                self.assertEqual(state["ExecutionClassificationSource"], "reader")
                self.assertNotEqual(state.get("QueueState"), "attention")
                self.assertEqual(
                    {path.name for path in outputs},
                    {"reader-brief.md", "synthesized-handoff.md"},
                )
                self.assertFalse((current / execution.MANUAL_ACTION_PLAN_FILE).exists())
        finally:
            opencode_adapter_handoff._prepare_reader = original_prepare  # type: ignore[attr-defined]
            opencode_adapter_roles._accept_role_once = original_accept  # type: ignore[attr-defined]
            opencode_adapter_roles._reader_correction_contract = original_correction  # type: ignore[attr-defined]

    def test_external_boundary_rejection_keeps_existing_one_retry_limit(self):
        original_prepare = opencode_adapter_handoff._prepare_reader  # type: ignore[attr-defined]
        original_accept = opencode_adapter_roles._accept_role_once  # type: ignore[attr-defined]
        original_correction = opencode_adapter_roles._reader_correction_contract  # type: ignore[attr-defined]
        try:
            execution_classification_hooks._install_reader_gate()
            boundary.install()
            with tempfile.TemporaryDirectory() as temp_dir:
                repo = Path(temp_dir)
                current = self._write_protocol_state(repo)
                result = current / "reader-brief.md"
                result.write_text(
                    self._block(
                        self._manual_payload(
                            "Implement missing API endpoints.",
                            "Write API code.",
                        )
                    ),
                    encoding="utf-8",
                )

                with self.assertRaises(OpenCodeAdapterError):
                    opencode_adapter_roles.accept_role("reader", repo, result)
                with self.assertRaises(OpenCodeAdapterError) as exhausted:
                    opencode_adapter_roles.accept_role("reader", repo, result)

                self.assertIn("protocol correction limit exhausted after one retry", str(exhausted.exception))
                self.assertFalse((current / execution.CLASSIFICATION_FILE).exists())
                self.assertFalse((current / execution.MANUAL_ACTION_PLAN_FILE).exists())
        finally:
            opencode_adapter_handoff._prepare_reader = original_prepare  # type: ignore[attr-defined]
            opencode_adapter_roles._accept_role_once = original_accept  # type: ignore[attr-defined]
            opencode_adapter_roles._reader_correction_contract = original_correction  # type: ignore[attr-defined]


if __name__ == "__main__":
    unittest.main()
