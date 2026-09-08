from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from automation import opencode_cli_text, role_output_contract


def _events(text: str) -> str:
    return json.dumps(
        {
            "type": "text",
            "part": {
                "type": "text",
                "text": text,
            },
        }
    ) + "\n"


class RoleOutputFallbackTests(unittest.TestCase):
    def test_synthesizer_captured_text_materializes_without_model_file_write(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            contract = role_output_contract.contract_for_role("synthesizer")
            outcome = opencode_cli_text.materialize_fallback_result(
                repo,
                contract,
                _events("# Handoff\n\nUse the existing seam."),
            )

            self.assertEqual(outcome.source, "captured-runtime-output")
            self.assertEqual(outcome.state, "succeeded")
            self.assertEqual(outcome.duplicate_state, "absent")
            self.assertEqual(
                (repo / ".autodev-run/current/synthesized-handoff.md").read_text(
                    encoding="utf-8"
                ),
                "# Handoff\n\nUse the existing seam.\n",
            )

    def test_planner_captured_text_materializes_six_section_plan(self):
        plan = """1) Where to look

automation/

2) Files / areas likely to touch

automation/opencode_cli_text.py

3) Assumptions

None.

4) Plan

Generalize fallback materialization.

5) Risks / gotchas

Keep retries bounded.

6) Recommended implementation approach

Reuse the role output contract.
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            outcome = opencode_cli_text.materialize_fallback_result(
                repo,
                role_output_contract.contract_for_role("planner"),
                _events(plan),
            )

            self.assertEqual(outcome.source, "captured-runtime-output")
            self.assertEqual(outcome.state, "succeeded")
            self.assertIn(
                "6) Recommended implementation approach",
                (repo / ".autodev-run/current/plan.md").read_text(encoding="utf-8"),
            )

    def test_empty_captured_output_preserves_valid_self_written_artifact(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = repo / ".autodev-run/current"
            current.mkdir(parents=True)
            artifact = current / "synthesized-handoff.md"
            artifact.write_text("legacy valid handoff\n", encoding="utf-8")

            outcome = opencode_cli_text.materialize_fallback_result(
                repo,
                role_output_contract.contract_for_role("synthesizer"),
                json.dumps({"type": "step_finish"}) + "\n",
            )

            self.assertEqual(outcome.source, "self-written-artifact")
            self.assertEqual(outcome.state, "succeeded")
            self.assertEqual(outcome.captured_state, "no-result")
            self.assertEqual(artifact.read_text(encoding="utf-8"), "legacy valid handoff\n")

    def test_conflicting_valid_sources_use_captured_result_explicitly(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = repo / ".autodev-run/current"
            current.mkdir(parents=True)
            artifact = current / "synthesized-handoff.md"
            artifact.write_text("legacy handoff\n", encoding="utf-8")

            outcome = opencode_cli_text.materialize_fallback_result(
                repo,
                role_output_contract.contract_for_role("synthesizer"),
                _events("captured handoff"),
            )

            self.assertEqual(outcome.source, "captured-runtime-output")
            self.assertEqual(
                outcome.duplicate_state,
                "conflicting-overridden-by-captured-runtime-output",
            )
            self.assertEqual(artifact.read_text(encoding="utf-8"), "captured handoff\n")

    def test_equivalent_duplicate_sources_are_deterministic(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = repo / ".autodev-run/current"
            current.mkdir(parents=True)
            artifact = current / "synthesized-handoff.md"
            artifact.write_text("same handoff\n", encoding="utf-8")

            outcome = opencode_cli_text.materialize_fallback_result(
                repo,
                role_output_contract.contract_for_role("synthesizer"),
                _events("same handoff"),
            )

            self.assertEqual(outcome.duplicate_state, "equivalent")
            self.assertEqual(artifact.read_text(encoding="utf-8"), "same handoff\n")

    def test_parser_rejection_does_not_create_artifact(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            outcome = opencode_cli_text.materialize_fallback_result(
                repo,
                role_output_contract.contract_for_role("planner"),
                _events("not a six section plan"),
            )

            self.assertEqual(outcome.state, "parser-rejected")
            self.assertFalse((repo / ".autodev-run/current/plan.md").exists())


if __name__ == "__main__":
    unittest.main()
