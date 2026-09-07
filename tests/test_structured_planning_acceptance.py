from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from automation import opencode_adapter_contract, opencode_adapter_roles, role_output_contract


class StructuredPlanningAcceptanceTests(unittest.TestCase):
    def _current(self, root: Path, role: str) -> Path:
        current = root / ".autodev-run" / "current"
        current.mkdir(parents=True)
        (current / f"ux-context-{role}.json").write_text(
            json.dumps(
                {
                    "ux_context": {
                        "journeys": ["create-task"],
                        "screens": ["task-editor"],
                        "states": ["task-editor-empty"],
                        "contract": "interaction-contract.md",
                        "principles": "principles.md",
                    },
                    "ux_context_fingerprint": "ux-accepted",
                }
            ),
            encoding="utf-8",
        )
        return current

    def test_native_planner_invalid_ux_reference_is_protocol_rejection_not_runtime_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = self._current(repo, "planner")
            contract = role_output_contract.contract_for_role("planner")
            assert contract is not None
            target = role_output_contract.materialize_structured_output(
                repo,
                contract,
                {
                    "plan": {
                        "where_to_look": "automation/",
                        "files_areas_likely_to_touch": "automation/runtime.py",
                        "assumptions": "",
                        "implementation_plan": "Implement it.",
                        "risks_gotchas": "",
                        "recommended_implementation_approach": "Test it.",
                    },
                    "ux": {
                        "constraints_addressed": [
                            {
                                "source_kind": "screen",
                                "source_id": "invented-screen",
                                "impact": "Must not become authority.",
                            }
                        ],
                        "open_questions": [],
                    },
                },
            )
            assert target is not None

            with self.assertRaises(opencode_adapter_contract.OpenCodeAdapterError):
                opencode_adapter_roles._accept_role_once("planner", current, target)

            self.assertTrue(target.is_file())

    def test_native_synthesizer_selected_ux_reference_accepts_normally(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = self._current(repo, "synthesizer")
            contract = role_output_contract.contract_for_role("synthesizer")
            assert contract is not None
            target = role_output_contract.materialize_structured_output(
                repo,
                contract,
                {
                    "handoff_markdown": "# Handoff\n\nPreserve the selected state.",
                    "ux": {
                        "constraints_addressed": [
                            {
                                "source_kind": "state",
                                "source_id": "task-editor-empty",
                                "impact": "Keep the empty state explicit.",
                            }
                        ],
                        "open_questions": [],
                    },
                },
            )
            assert target is not None

            outputs = opencode_adapter_roles._accept_role_once("synthesizer", current, target)

            self.assertEqual(len(outputs), 1)
            self.assertTrue(outputs[0].samefile(target))

    def test_protocol_correction_drops_native_ux_sidecar_before_next_attempt(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = self._current(repo, "planner")
            plan = current / "plan.md"
            plan.write_text("invalid plan\n", encoding="utf-8")
            sidecar = current / "structured-ux-planner.json"
            sidecar.write_text(
                json.dumps(
                    {
                        "constraints_addressed": [
                            {
                                "source_kind": "screen",
                                "source_id": "invented-screen",
                                "impact": "must not survive correction",
                            }
                        ],
                        "open_questions": [],
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaises(opencode_adapter_contract.OpenCodeAdapterError):
                opencode_adapter_roles._raise_contract_rejection(
                    current,
                    "planner",
                    plan,
                    opencode_adapter_contract.OpenCodeAdapterError(
                        "structured UX reference rejected"
                    ),
                )

            self.assertFalse(sidecar.exists())
            self.assertTrue((current / "contract-correction-planner.md").is_file())
            self.assertTrue(plan.is_file())


if __name__ == "__main__":
    unittest.main()
