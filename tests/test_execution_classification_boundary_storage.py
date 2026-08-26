from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from automation import execution_classification_boundary as boundary
from automation import workflow_stages


class ExecutionClassificationBoundaryStorageTests(unittest.TestCase):
    def test_accepted_external_boundary_evidence_is_durable_and_clearable(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            current = Path(temp_dir)
            workflow_stages.write_state(
                current,
                {
                    "Status": "Prepared",
                    "IssueNumber": 176,
                },
            )
            evidence = (
                boundary.ExternalBoundaryEvidence(
                    criterion="Complete publisher identity validation.",
                    boundary_kind="human-legal-provider-approval",
                    human_action="Complete identity approval with the certificate provider.",
                    external_system="certificate provider",
                    unavailable_state="publisher identity approval is not yet available",
                    why_unsupported="provider identity approval requires an authorized human/legal workflow",
                ),
            )

            boundary._persist_external_boundary_evidence(current, evidence)

            artifact = current / boundary.EXTERNAL_BOUNDARY_FILE
            self.assertTrue(artifact.is_file())
            payload = json.loads(artifact.read_text(encoding="utf-8"))
            self.assertEqual(payload[0]["boundary_kind"], "human-legal-provider-approval")
            state = workflow_stages.read_state(current)
            self.assertEqual(state["ExternalBoundaryEvidence"], payload)
            self.assertEqual(
                state["ExternalBoundaryEvidenceFile"],
                ".autodev-run/current/execution-external-boundaries.json",
            )

            boundary._persist_external_boundary_evidence(current, ())

            self.assertFalse(artifact.exists())
            state = workflow_stages.read_state(current)
            self.assertNotIn("ExternalBoundaryEvidence", state)
            self.assertNotIn("ExternalBoundaryEvidenceFile", state)


if __name__ == "__main__":
    unittest.main()
