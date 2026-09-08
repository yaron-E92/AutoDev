from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from automation import (
    opencode_adapter_roles,
    opencode_role_runtime,
    opencode_structured_output,
    role_output_contract,
    role_runtime,
)


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


class StructuredFallbackMaterializationTests(unittest.TestCase):
    def _runtime(self) -> opencode_role_runtime.OpenCodeRoleRuntime:
        runtime = opencode_role_runtime.OpenCodeRoleRuntime()
        runtime._mappings = {
            role: {
                "agent": f"autodev-{role}",
                "model": "ollama/gpt-oss:20b-autodev",
                "source": "test",
                "inherits_from": "",
            }
            for role in ("reader", "synthesizer", "planner", "verifier")
        }
        return runtime

    def _context(self, repo: Path, role: str, *, phase: str = "work"):
        return role_runtime.RoleInvocationContext(
            repo=repo,
            role=role,
            prompt=f"run {role}",
            phase=phase,
            output_contract=role_output_contract.contract_for_role(role),
        )

    def _patches(self, native_side_effect):
        return (
            patch.object(
                opencode_role_runtime.opencode_cli,
                "resolve_opencode_cli",
                return_value="opencode",
            ),
            patch.object(
                opencode_role_runtime.privacy,
                "load_policy",
                return_value=SimpleNamespace(enabled=False),
            ),
            patch.object(
                opencode_role_runtime.opencode_structured_output,
                "invoke",
                side_effect=native_side_effect,
            ),
        )

    def test_events_179_reader_then_synthesizer_need_no_model_file_writes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            responses = iter(
                [
                    _events("Reader factual handoff for Events #179."),
                    _events("Synthesized handoff for Events #179."),
                ]
            )

            def runner(command, **kwargs):
                return SimpleNamespace(returncode=0, stdout=next(responses), stderr="")

            runtime = self._runtime()
            cli_patch, privacy_patch, native_patch = self._patches(
                [
                    opencode_structured_output.StructuredOutputExhausted(
                        "reader schema exhausted", retries=2
                    ),
                    opencode_structured_output.StructuredOutputUnavailable(
                        "synth native unavailable"
                    ),
                ]
            )
            with cli_patch, privacy_patch, native_patch:
                reader = runtime.invoke(self._context(repo, "reader"), runner=runner)
                synthesizer = runtime.invoke(
                    self._context(repo, "synthesizer"), runner=runner
                )

            self.assertEqual(reader.termination, "completed")
            self.assertEqual(
                reader.structured_output_state,
                opencode_role_runtime.READER_SCHEMA_FALLBACK_STATE,
            )
            self.assertEqual(synthesizer.termination, "completed")
            self.assertEqual(synthesizer.structured_output_mode, "fallback-text")
            self.assertEqual(synthesizer.structured_output_state, "native-unavailable")
            self.assertEqual(
                (repo / ".autodev-run/current/reader-brief.md").read_text(
                    encoding="utf-8"
                ),
                "Reader factual handoff for Events #179.\n",
            )
            synth = repo / ".autodev-run/current/synthesized-handoff.md"
            self.assertEqual(
                synth.read_text(encoding="utf-8"),
                "Synthesized handoff for Events #179.\n",
            )
            accepted = opencode_adapter_roles._accept_role_once(
                "synthesizer",
                repo / ".autodev-run/current",
                synth,
            )
            self.assertEqual(accepted, [synth])

    def test_planner_native_unavailable_materializes_captured_plan(self):
        plan = """1) Where to look

automation/

2) Files / areas likely to touch

automation/opencode_cli_text.py

3) Assumptions

None.

4) Plan

Generalize the fallback boundary.

5) Risks / gotchas

Do not restart native output.

6) Recommended implementation approach

Materialize captured role text in Python.
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            cli_patch, privacy_patch, native_patch = self._patches(
                opencode_structured_output.StructuredOutputUnavailable("old runtime")
            )
            with cli_patch, privacy_patch, native_patch:
                result = self._runtime().invoke(
                    self._context(repo, "planner"),
                    runner=lambda *a, **k: SimpleNamespace(
                        returncode=0,
                        stdout=_events(plan),
                        stderr="",
                    ),
                )

            self.assertEqual(result.structured_output_state, "native-unavailable")
            artifact = repo / ".autodev-run/current/plan.md"
            self.assertTrue(artifact.is_file())
            self.assertIn("6) Recommended implementation approach", artifact.read_text(encoding="utf-8"))

    def test_correction_fallback_materializes_without_file_write_and_records_phase(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = repo / ".autodev-run/current"
            current.mkdir(parents=True)
            (current / "run-diagnostics.json").write_text(
                json.dumps({"role_invocations": {"synthesizer": 1}}),
                encoding="utf-8",
            )
            context = self._context(repo, "synthesizer", phase="correction")
            runtime = self._runtime()

            result = runtime._invoke_cli(
                context,
                executable="opencode",
                repo=repo,
                model="ollama/gpt-oss:20b-autodev",
                environment={},
                runner=lambda *a, **k: SimpleNamespace(
                    returncode=0,
                    stdout=_events("Corrected synthesized handoff."),
                    stderr="",
                ),
                fallback_state="native-unavailable",
            )

            self.assertEqual(result.returncode, 0)
            artifact = current / "synthesized-handoff.md"
            self.assertEqual(
                artifact.read_text(encoding="utf-8"),
                "Corrected synthesized handoff.\n",
            )
            diagnostics = json.loads(
                (current / "run-diagnostics.json").read_text(encoding="utf-8")
            )
            materialization = diagnostics["fallback_materialization"][
                "synthesizer:correction"
            ]
            self.assertEqual(materialization["source"], "captured-runtime-output")
            self.assertEqual(materialization["state"], "succeeded")

    def test_correction_after_fallback_does_not_start_second_native_sequence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = repo / ".autodev-run/current"
            current.mkdir(parents=True)
            (current / "run-diagnostics.json").write_text(
                json.dumps(
                    {
                        "role_invocations": {"synthesizer": 1},
                        "last_structured_output": {
                            "role": "synthesizer",
                            "mode": "fallback-text",
                            "state": "native-unavailable",
                        },
                    }
                ),
                encoding="utf-8",
            )
            runtime = self._runtime()
            context = self._context(repo, "synthesizer", phase="correction")

            with patch.object(
                opencode_role_runtime.opencode_cli,
                "resolve_opencode_cli",
                return_value="opencode",
            ), patch.object(
                opencode_role_runtime.privacy,
                "load_policy",
                return_value=SimpleNamespace(enabled=False),
            ), patch.object(
                opencode_role_runtime.opencode_structured_output,
                "invoke",
            ) as native:
                result = runtime.invoke(
                    context,
                    runner=lambda *a, **k: SimpleNamespace(
                        returncode=0,
                        stdout=_events("Corrected captured handoff."),
                        stderr="",
                    ),
                )

            native.assert_not_called()
            self.assertEqual(result.termination, "completed")
            self.assertEqual(result.structured_output_mode, "fallback-text")
            self.assertEqual(
                result.structured_output_state,
                opencode_role_runtime.FALLBACK_CORRECTION_STATE,
            )
            self.assertEqual(
                (current / "synthesized-handoff.md").read_text(encoding="utf-8"),
                "Corrected captured handoff.\n",
            )


if __name__ == "__main__":
    unittest.main()
