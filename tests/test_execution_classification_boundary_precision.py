from __future__ import annotations

import json
import unittest

from automation import execution_classification as execution
from automation import execution_classification_boundary as boundary


class ExecutionClassificationBoundaryPrecisionTests(unittest.TestCase):
    def _block(self, payload: dict[str, object]) -> str:
        return (
            execution.CLASSIFICATION_BLOCK_START
            + "\n"
            + json.dumps(payload)
            + "\n"
            + execution.CLASSIFICATION_BLOCK_END
            + "\n"
        )

    def _payload(
        self,
        criterion: str,
        action: str,
        *,
        kind: str,
        external_system: str,
        unavailable_state: str,
        why_unsupported: str,
    ) -> dict[str, object]:
        return {
            "classification": "manual-external",
            "reason": "A genuine provider-owned prerequisite is not available.",
            "autonomous_criteria": [],
            "manual_criteria": [criterion],
            "human_actions": [action],
            "resume_evidence": ["Record only the non-secret provider resource identifier or approval state."],
            "manual_prerequisite_blocks_implementation": True,
            "autonomous_subset_independent": False,
            "external_boundaries": [
                {
                    "criterion": criterion,
                    "boundary_kind": kind,
                    "human_action": action,
                    "external_system": external_system,
                    "unavailable_state": unavailable_state,
                    "why_unsupported": why_unsupported,
                }
            ],
        }

    def test_provider_service_account_creation_is_not_mistaken_for_repository_service_code(self):
        criterion = "Provision the provider service account and obtain organization approval."
        action = "Create the provider service account and complete administrator approval."
        payload = self._payload(
            criterion,
            action,
            kind="human-legal-provider-approval",
            external_system="external cloud provider organization",
            unavailable_state="the provider service account and organization approval do not exist yet",
            why_unsupported="organization administrators must approve the account outside repository and GitHub tooling",
        )

        evidence = boundary.validate_reader_external_boundary(self._block(payload))

        self.assertEqual(len(evidence), 1)

    def test_code_signing_certificate_issuance_is_not_mistaken_for_source_code_generation(self):
        criterion = "Obtain an externally issued production code-signing certificate."
        action = "Generate the code-signing certificate through the certificate authority identity workflow."
        payload = self._payload(
            criterion,
            action,
            kind="human-legal-provider-approval",
            external_system="public certificate authority",
            unavailable_state="publisher validation and certificate issuance are incomplete",
            why_unsupported="the certificate authority requires publisher identity validation before issuance",
        )

        evidence = boundary.validate_reader_external_boundary(self._block(payload))

        self.assertEqual(len(evidence), 1)


if __name__ == "__main__":
    unittest.main()
