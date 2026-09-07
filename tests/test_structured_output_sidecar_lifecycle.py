from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from automation import opencode_adapter_protocol, opencode_adapter_roles
from automation.opencode_adapter_contract import OpenCodeAdapterError


class StructuredOutputSidecarLifecycleTests(unittest.TestCase):
    def _write_state(self, current: Path, role: str) -> None:
        (current / "state.json").write_text(
            json.dumps(
                {
                    "AcceptedRoleArtifacts": {
                        role: {
                            "artifact": f".autodev-run/current/{role}.out",
                            "sha256": "old",
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        (current / "run-diagnostics.json").write_text("{}\n", encoding="utf-8")

    def test_new_role_invocation_clears_only_stale_structured_sidecars(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            current = Path(temp_dir) / ".autodev-run" / "current"
            current.mkdir(parents=True)

            self._write_state(current, "implementer")
            canonical = current / "commit-message.txt"
            canonical.write_text("existing canonical artifact\n", encoding="utf-8")
            completion = current / "structured-completion-implementer.json"
            completion.write_text("{}\n", encoding="utf-8")
            ux = current / "structured-ux-implementer.json"
            ux.write_text("{}\n", encoding="utf-8")

            opencode_adapter_protocol._begin_role_invocation(current, "implementer")

            self.assertTrue(canonical.is_file())
            self.assertFalse(completion.exists())
            self.assertFalse(ux.exists())
            state = json.loads((current / "state.json").read_text(encoding="utf-8"))
            self.assertNotIn("implementer", state["AcceptedRoleArtifacts"])

            self._write_state(current, "reader")
            reader_ux = current / "structured-ux-reader.json"
            reader_evidence = current / "structured-reader-evidence.json"
            reader_ux.write_text("{}\n", encoding="utf-8")
            reader_evidence.write_text("[]\n", encoding="utf-8")

            opencode_adapter_protocol._begin_role_invocation(current, "reader")

            self.assertFalse(reader_ux.exists())
            self.assertFalse(reader_evidence.exists())

    def test_protocol_correction_clears_native_sidecars_before_next_attempt(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            current = Path(temp_dir) / ".autodev-run" / "current"
            current.mkdir(parents=True)
            (current / "run-diagnostics.json").write_text("{}\n", encoding="utf-8")

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

            with self.assertRaises(OpenCodeAdapterError):
                opencode_adapter_roles._raise_contract_rejection(
                    current,
                    "planner",
                    plan,
                    OpenCodeAdapterError("structured UX reference rejected"),
                )

            self.assertFalse(sidecar.exists())
            self.assertTrue((current / "contract-correction-planner.md").is_file())
            self.assertTrue(plan.is_file())


if __name__ == "__main__":
    unittest.main()
