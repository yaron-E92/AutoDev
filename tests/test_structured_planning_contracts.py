from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from automation import (
    opencode_adapter_contract,
    opencode_adapter_roles,
    planner_output,
    role_output_contract,
)


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
            accepted = opencode_adapter_roles._accept_role_once("planner", current, target)
            self.assertEqual(len(accepted), 1)
            self.assertTrue(accepted[0].samefile(target))

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
            accepted = opencode_adapter_roles._accept_role_once("synthesizer", current, target)
            self.assertEqual(len(accepted), 1)
            self.assertTrue(accepted[0].samefile(target))

    def test_planning_ux_reference_outside_selected_context_fails_at_shared_acceptance(self):
        contract = role_output_contract.contract_for_role("planner")
        assert contract is not None
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = self._current_with_ux(repo, "planner")
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
            target = role_output_contract.materialize_structured_output(repo, contract, payload)
            assert target is not None
            with self.assertRaises(opencode_adapter_contract.OpenCodeAdapterError) as raised:
                opencode_adapter_roles._accept_role_once("planner", current, target)
            self.assertIn("outside the effective selected UX context", str(raised.exception))

    def test_planning_contracts_are_bounded_and_never_embed_customer_specific_ux_ids(self):
        for role in ("planner", "synthesizer"):
            contract = role_output_contract.contract_for_role(role)
            assert contract is not None
            schema = contract.schema()
            schema_text = json.dumps(schema, sort_keys=True)
            self.assertIn("source_id", schema_text)
            self.assertNotIn("create-task", schema_text)
            self.assertNotIn("task-editor-empty", schema_text)
            ux = schema["properties"]["ux"]["properties"]
            self.assertLessEqual(ux["constraints_addressed"]["maxItems"], 128)
            self.assertLessEqual(ux["open_questions"]["maxItems"], 64)
            if role == "synthesizer":
                self.assertEqual(schema["properties"]["handoff_markdown"]["maxLength"], 30000)
            else:
                plan = schema["properties"]["plan"]["properties"]
                self.assertTrue(all(value.get("maxLength", 0) > 0 for value in plan.values()))

    def test_planning_snapshot_identity_binds_contract_and_effective_ux_context(self):
        for role in ("planner", "synthesizer"):
            original = {
                "fingerprint": "a" * 64,
                "safe_metadata": {"runtime": "opencode", "model": "provider/model"},
            }
            first = role_output_contract.bind_role_snapshot(
                original,
                role,
                ux_context_fingerprint="ux-a",
            )
            second = role_output_contract.bind_role_snapshot(
                first,
                role,
                ux_context_fingerprint="ux-b",
            )
            self.assertNotEqual(first["fingerprint"], original["fingerprint"])
            self.assertNotEqual(second["fingerprint"], first["fingerprint"])
            binding = second["safe_metadata"]["role_output_binding"]
            self.assertEqual(binding["ux_context_fingerprint"], "ux-b")
            self.assertEqual(binding["contract"]["identity"], f"autodev.{role}/v1")


if __name__ == "__main__":
    unittest.main()
