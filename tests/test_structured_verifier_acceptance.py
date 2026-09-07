from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from automation import opencode_adapter_roles
from automation.semantic_contract import SemanticVerifierError


class StructuredVerifierAcceptanceTests(unittest.TestCase):
    def _current(self, root: Path) -> Path:
        current = root / ".autodev-run" / "current"
        current.mkdir(parents=True)
        (current / "issue.md").write_text("# Issue\n\nVerify the selected UX state.\n", encoding="utf-8")
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
                    "ux_context_fingerprint": "autodev-owned-fingerprint",
                }
            ),
            encoding="utf-8",
        )
        return current

    def _payload(self, source_id: str) -> dict[str, object]:
        return {
            "verdict": "pass",
            "requirements": [],
            "findings": [],
            "repair_brief": "",
            "ux_findings": [
                {
                    "source_kind": "state",
                    "source_id": source_id,
                    "status": "satisfied",
                    "evidence": "The diff preserves the selected empty-state behavior.",
                    "required_change": "",
                }
            ],
        }

    def test_shared_verifier_acceptance_accepts_selected_ux_reference(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            current = self._current(Path(temp_dir))
            source = current / "verification-result.json"
            source.write_text(json.dumps(self._payload("task-editor-empty")), encoding="utf-8")

            outputs = opencode_adapter_roles._accept_role_once("verifier", current, source)

            self.assertTrue(outputs)
            accepted = json.loads((current / "verification-result.json").read_text(encoding="utf-8"))
            self.assertEqual(accepted["ux_findings"][0]["source_id"], "task-editor-empty")

    def test_shared_verifier_acceptance_rejects_invented_ux_reference(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            current = self._current(Path(temp_dir))
            source = current / "verification-result.json"
            source.write_text(json.dumps(self._payload("invented-state")), encoding="utf-8")

            with self.assertRaises(SemanticVerifierError) as raised:
                opencode_adapter_roles._accept_role_once("verifier", current, source)

            self.assertEqual(
                raised.exception.classification,
                "ux_reference_outside_effective_context",
            )
            self.assertIn("outside the effective selected UX context", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
