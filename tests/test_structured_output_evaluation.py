from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from automation import structured_output_evaluation


class StructuredOutputEvaluationTests(unittest.TestCase):
    def _write_attempt(self, directory: Path, name: str, payload: dict[str, object]) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        (directory / name).write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def test_evaluation_separates_native_fallback_corrections_and_schema_retries(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            attempts = (
                repo
                / structured_output_evaluation.CURRENT_DIR
                / structured_output_evaluation.ROLE_ATTEMPT_DIR
            )
            self._write_attempt(
                attempts,
                "planner-01-01-initial.json",
                {
                    "role": "planner",
                    "attempt_kind": "initial",
                    "accepted": True,
                    "structured_output_mode": "native-validated",
                    "schema_retry_count": 2,
                    "failure_classification": "",
                    "stdout_excerpt": "sensitive model text is ignored by evaluation",
                },
            )
            self._write_attempt(
                attempts,
                "reader-01-01-initial.json",
                {
                    "role": "reader",
                    "attempt_kind": "initial",
                    "accepted": False,
                    "structured_output_mode": "fallback-text",
                    "schema_retry_count": 0,
                    "failure_classification": "role-protocol-failure",
                    "validation_error": "malformed output",
                },
            )
            self._write_attempt(
                attempts,
                "reader-01-02-protocol-correction.json",
                {
                    "role": "reader",
                    "attempt_kind": "protocol-correction",
                    "accepted": True,
                    "structured_output_mode": "fallback-text",
                    "schema_retry_count": 0,
                    "failure_classification": "",
                },
            )

            result = structured_output_evaluation.evaluate(repo)

            self.assertEqual(result["totals"]["attempts"], 3)
            self.assertEqual(result["totals"]["native"]["attempts"], 1)
            self.assertEqual(result["totals"]["fallback"]["attempts"], 2)
            self.assertEqual(result["totals"]["schema_retry_count"], 2)
            self.assertEqual(result["totals"]["protocol_correction_attempts"], 1)
            self.assertEqual(result["totals"]["protocol_rejections"], 1)
            self.assertNotIn("sensitive model text", json.dumps(result))

    def test_evaluation_reports_mechanical_ux_evidence_without_completion_self_attestation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = repo / structured_output_evaluation.CURRENT_DIR
            current.mkdir(parents=True)

            for role in ("planner", "verifier", "implementer"):
                (current / f"ux-context-{role}.json").write_text(
                    json.dumps(
                        {
                            "ux_context_fingerprint": f"ux-{role}",
                            "ux_context": {"journeys": ["create-item"]},
                        }
                    ),
                    encoding="utf-8",
                )

            (current / "structured-ux-planner.json").write_text(
                json.dumps(
                    {
                        "constraints_addressed": [
                            {
                                "source_kind": "journey",
                                "source_id": "create-item",
                                "impact": "changes the implementation plan",
                            }
                        ],
                        "open_questions": [],
                    }
                ),
                encoding="utf-8",
            )
            (current / "verification-result.json").write_text(
                json.dumps(
                    {
                        "ux_findings": [
                            {
                                "source_kind": "journey",
                                "source_id": "create-item",
                                "status": "satisfied",
                                "evidence": "bounded evidence",
                                "required_change": "",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            result = structured_output_evaluation.evaluate(repo)
            ux = result["ux"]

            self.assertEqual(
                ux["active_roles"],
                ["implementer", "planner", "verifier"],
            )
            self.assertEqual(
                ux["evidence_applicable_roles"],
                ["planner", "verifier"],
            )
            self.assertEqual(ux["evidence_roles"], ["planner", "verifier"])
            self.assertEqual(ux["missing_evidence_roles"], [])


if __name__ == "__main__":
    unittest.main()
