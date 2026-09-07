from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from automation import role_output_contract


class RoleOutputContractTests(unittest.TestCase):
    def test_verifier_contract_is_versioned_static_schema(self):
        contract = role_output_contract.contract_for_role("verifier")
        self.assertIsNotNone(contract)
        assert contract is not None
        self.assertEqual(contract.identity, "autodev.semantic-verifier/v1")
        schema = contract.schema()
        self.assertEqual(schema["type"], "object")
        self.assertIn("verdict", schema["properties"])
        self.assertIn("ux_findings", schema["properties"])
        self.assertEqual(len(contract.schema_sha256()), 64)
        serialized = json.dumps(schema, sort_keys=True)
        self.assertNotIn("issue.md", serialized)
        self.assertNotIn("ux_context_fingerprint", serialized)

    def test_materialize_verifier_output_uses_existing_protocol_artifact(self):
        contract = role_output_contract.contract_for_role("verifier")
        assert contract is not None
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            payload = {
                "verdict": "pass",
                "requirements": [],
                "findings": [],
                "repair_brief": "",
            }
            target = role_output_contract.materialize_structured_output(
                repo, contract, payload
            )
            self.assertEqual(
                target,
                repo / ".autodev-run" / "current" / "verification-result.json",
            )
            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), payload)

    def test_ux_references_must_belong_to_effective_selected_context(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            current = Path(temp_dir)
            (current / "ux-context-verifier.json").write_text(
                json.dumps(
                    {
                        "ux_context": {
                            "journeys": ["create-task"],
                            "screens": ["task-editor"],
                            "states": ["task-editor-empty"],
                            "contract": "interaction-contract.md",
                            "principles": "principles.md",
                        },
                        "ux_context_fingerprint": "abc",
                    }
                ),
                encoding="utf-8",
            )
            role_output_contract.validate_ux_references(
                current,
                "verifier",
                {
                    "ux_findings": [
                        {
                            "source_kind": "state",
                            "source_id": "task-editor-empty",
                            "status": "satisfied",
                            "evidence": "covered",
                            "required_change": "",
                        }
                    ]
                },
            )
            with self.assertRaises(role_output_contract.RoleOutputContractError):
                role_output_contract.validate_ux_references(
                    current,
                    "verifier",
                    {
                        "ux_findings": [
                            {
                                "source_kind": "state",
                                "source_id": "not-selected",
                                "status": "violated",
                                "evidence": "claim",
                                "required_change": "repair",
                            }
                        ]
                    },
                )

    def test_ux_findings_without_effective_context_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaises(role_output_contract.RoleOutputContractError):
                role_output_contract.validate_ux_references(
                    Path(temp_dir),
                    "verifier",
                    {
                        "ux_findings": [
                            {
                                "source_kind": "journey",
                                "source_id": "create-task",
                                "status": "unverifiable",
                                "evidence": "",
                                "required_change": "",
                            }
                        ]
                    },
                )


if __name__ == "__main__":
    unittest.main()
