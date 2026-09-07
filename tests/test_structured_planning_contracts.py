from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from automation import planner_output, role_output_contract


class StructuredPlanningContractTests(unittest.TestCase):
    def _current_with_ux(self, root: Path, role: str) -> Path:
        current = root / ".autodev-run" / "current"
        current.mkdir(parents=True, exist_ok=True)
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
                    "ux_context_fingerprint": "ux-1",
                }
            ),
            encoding="utf-8",
        )
        return current

    def test_planner_native_payload_materializes_existing_six_section_artifact(self):
        contract = role_output_contract.contract_for_role("planner")
        assert contract is not None
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = self._current_with_ux(repo, "planner")
            payload = {
                "plan": {
                    "where_to_look": "automation/runtime.py",
                    "files_areas_likely_to_touch": "automation/",
                    "assumptions": "Existing runtime contract remains stable.",
                    "implementation_plan": "1. Add the capability seam.\n2. Cover it with tests.",
                    "risks_gotchas": "Keep fallback behavior.",
                    "recommended_implementation_approach": "Implement incrementally.",
                },
                "ux": {
                    "constraints_addressed": [
                        {
                            "source_kind": "journey",
                            "source_id": "create-task",
                            "impact": "Preserve the task-first flow.",
                        }
                    ],
                    "open_questions": [],
                },
            }
            target = role_output_contract.materialize_structured_output(repo, contract, payload)
            assert target is not None
            text = target.read_text(encoding="utf-8")
            self.assertEqual(planner_output.sanitize_planner_output(text), text)
            for heading in planner_output.REQUIRED_PLAN_HEADINGS:
                self.assertIn(heading, text)
            sidecar = json.loads(
                (current / "structured-ux-planner.json").read_text(encoding="utf-8")
            )
            self.assertEqual(sidecar["constraints_addressed"][0]["source_id"], "create-task")

    def test_synthesizer_native_payload_preserves_existing_handoff_artifact(self):
        contract = role_output_contract.contract_for_role("synthesizer")
        assert contract is not None
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = self._current_with_ux(repo, "synthesizer")
            payload = {
                "handoff_markdown": "# Repository handoff\n\nUse the existing runtime seam.",
                "ux": {
                    "constraints_addressed": [
                        {
                            "source_kind": "state",
                            "source_id": "task-editor-empty",
                            "impact": "Keep empty-state behavior explicit.",
                        }
                    ],
                    "open_questions": ["Confirm the loading state."],
                },
            }
            target = role_output_contract.materialize_structured_output(repo, contract, payload)
            assert target is not None
            self.assertEqual(
                target.read_text(encoding="utf-8"),
                "# Repository handoff\n\nUse the existing runtime seam.\n",
            )
            sidecar = json.loads(
                (current / "structured-ux-synthesizer.json").read_text(encoding="utf-8")
            )
            self.assertEqual(sidecar["open_questions"], ["Confirm the loading state."])

    def test_planning_ux_reference_outside_selected_context_fails_closed(self):
        contract = role_output_contract.contract_for_role("planner")
        assert contract is not None
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            self._current_with_ux(repo, "planner")
            payload = {
                "plan": {
                    "where_to_look": "automation/",
                    "files_areas_likely_to_touch": "automation/",
                    "assumptions": "",
                    "implementation_plan": "Implement.",
                    "risks_gotchas": "",
                    "recommended_implementation_approach": "Test it.",
                },
                "ux": {
                    "constraints_addressed": [
                        {
                            "source_kind": "screen",
                            "source_id": "invented-screen",
                            "impact": "This must not be trusted.",
                        }
                    ],
                    "open_questions": [],
                },
            }
            with self.assertRaises(role_output_contract.RoleOutputContractError):
                role_output_contract.materialize_structured_output(repo, contract, payload)

    def test_planning_contracts_never_embed_customer_specific_ux_ids(self):
        for role in ("planner", "synthesizer"):
            contract = role_output_contract.contract_for_role(role)
            assert contract is not None
            schema_text = json.dumps(contract.schema(), sort_keys=True)
            self.assertIn("source_id", schema_text)
            self.assertNotIn("create-task", schema_text)
            self.assertNotIn("task-editor-empty", schema_text)


if __name__ == "__main__":
    unittest.main()
