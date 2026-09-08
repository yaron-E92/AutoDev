from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from automation import (
    opencode_role_runtime,
    role_output_contract,
    role_runtime,
)


def _event(text: str) -> str:
    return json.dumps(
        {"type": "text", "part": {"type": "text", "text": text}}
    ) + "\n"


class StructuredFallbackRetryBudgetTests(unittest.TestCase):
    def test_explicit_fallback_only_context_is_emulated_and_skips_native(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            runtime = opencode_role_runtime.OpenCodeRoleRuntime()
            runtime._mappings = {
                "synthesizer": {
                    "agent": "autodev-synthesizer",
                    "model": "ollama/gpt-oss:20b-autodev",
                    "source": "test",
                    "inherits_from": "",
                }
            }
            context = role_runtime.RoleInvocationContext(
                repo=repo,
                role="synthesizer",
                prompt="correct the synthesizer result",
                phase="correction",
                output_contract=role_output_contract.contract_for_role("synthesizer"),
                fallback_text_only=True,
            )

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
                capability = runtime.structured_output_capability(context)
                result = runtime.invoke(
                    context,
                    runner=lambda *a, **k: SimpleNamespace(
                        returncode=0,
                        stdout=_event("corrected handoff"),
                        stderr="",
                    ),
                )

            self.assertEqual(capability, role_output_contract.CAPABILITY_EMULATED)
            native.assert_not_called()
            self.assertEqual(result.structured_output_mode, "fallback-text")
            self.assertEqual(
                result.structured_output_state,
                opencode_role_runtime.FALLBACK_CORRECTION_STATE,
            )
            self.assertEqual(
                (repo / ".autodev-run/current/synthesized-handoff.md").read_text(
                    encoding="utf-8"
                ),
                "corrected handoff\n",
            )


if __name__ == "__main__":
    unittest.main()
