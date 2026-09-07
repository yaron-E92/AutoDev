from __future__ import annotations

import unittest

from automation.semantic_contract import SemanticVerifierError
from automation.semantic_schema import parse_semantic_value


class StructuredVerifierSchemaTests(unittest.TestCase):
    def test_decoded_structured_value_reuses_semantic_validation(self):
        result = parse_semantic_value(
            {
                "verdict": "pass",
                "requirements": [
                    {"criterion": "works", "status": "met", "evidence": ["test"]}
                ],
                "findings": [],
                "repair_brief": "",
                "ux_findings": [
                    {
                        "source_kind": "state",
                        "source_id": "editor-empty",
                        "status": "satisfied",
                        "evidence": "covered by implementation",
                        "required_change": "",
                    }
                ],
            },
            expected_criteria=["works"],
        )
        self.assertEqual(result["verdict"], "pass")
        self.assertEqual(result["ux_findings"][0]["source_id"], "editor-empty")

    def test_schema_valid_pass_cannot_hide_violated_ux_authority(self):
        with self.assertRaises(SemanticVerifierError) as raised:
            parse_semantic_value(
                {
                    "verdict": "pass",
                    "requirements": [
                        {"criterion": "works", "status": "met", "evidence": []}
                    ],
                    "findings": [],
                    "repair_brief": "",
                    "ux_findings": [
                        {
                            "source_kind": "journey",
                            "source_id": "create-task",
                            "status": "violated",
                            "evidence": "flow differs",
                            "required_change": "restore required flow",
                        }
                    ],
                },
                expected_criteria=["works"],
            )
        self.assertEqual(raised.exception.classification, "inconsistent_semantic_verdict")

    def test_structured_shape_does_not_make_missing_requirement_authoritative(self):
        with self.assertRaises(SemanticVerifierError) as raised:
            parse_semantic_value(
                {
                    "verdict": "pass",
                    "requirements": [],
                    "findings": [],
                    "repair_brief": "",
                },
                expected_criteria=["must still be checked"],
            )
        self.assertEqual(
            raised.exception.classification,
            "incomplete_semantic_requirements",
        )


if __name__ == "__main__":
    unittest.main()
