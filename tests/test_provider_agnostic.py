import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from automation import prompt_runner
from automation.model_providers import ModelProvider, ProviderResponse


REPO_ROOT = Path(__file__).resolve().parents[1]


def six_section_plan():
    return """1) Where to look
- automation/model_providers.py
2) Files / areas likely to touch
- automation/model_providers.py
3) Assumptions
- Shared provider path.
4) Plan
- Apply the narrow change.
5) Risks / gotchas
- Preserve output contracts.
6) Recommended implementation approach
- Option A: use the shared provider.
"""


class TelemetryProvider(ModelProvider):
    def invoke(self, prompt, *, model, timeout_seconds):
        return ProviderResponse(
            six_section_plan(),
            {"usage": {"input_tokens": 10, "output_tokens": 20}, "reported_cost": 0.0},
        )


class ProviderAgnosticTests(unittest.TestCase):
    def test_prompt_runner_resolves_role_and_separates_telemetry(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            profile = root / "profile.json"
            prompt = root / "prompt.md"
            output = root / "plan.md"
            telemetry = root / "telemetry.json"
            profile.write_text(
                json.dumps(
                    {
                        "version": 2,
                        "name": "mixed-test",
                        "roles": {
                            "planner": {
                                "transport": "mock",
                                "model": "planner-model",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            prompt.write_text("Plan this issue.", encoding="utf-8")

            with mock.patch.object(prompt_runner, "create_provider", return_value=TelemetryProvider()):
                code = prompt_runner.run(
                    [
                        "--role", "planner",
                        "--provider-profile", str(profile),
                        "--prompt-file", str(prompt),
                        "--output-file", str(output),
                        "--telemetry-file", str(telemetry),
                    ]
                )

            self.assertEqual(code, 0)
            self.assertNotIn("input_tokens", output.read_text(encoding="utf-8"))
            records = json.loads(telemetry.read_text(encoding="utf-8"))
            self.assertEqual(records[0]["role"], "planner")
            self.assertEqual(records[0]["profile_name"], "mixed-test")
            self.assertEqual(records[0]["usage"]["output_tokens"], 20)

    def test_repair_alias_normalizes_to_fixer(self):
        self.assertEqual(prompt_runner.normalize_role("repair"), "fixer")
        self.assertEqual(prompt_runner.normalize_role("implementer"), "implementer")

    def test_mixed_profile_keeps_openrouter_roles_free_only(self):
        profile = json.loads(
            (REPO_ROOT / "examples" / "providers" / "groq-openrouter-free.json").read_text(encoding="utf-8")
        )

        self.assertEqual(profile["roles"]["planner"]["api_key_env"], "GROQ_API_KEY")
        for role in ("implementer", "fixer"):
            self.assertTrue(profile["roles"][role]["model"].endswith(":free"))
            self.assertTrue(profile["roles"][role]["free_only"])
            self.assertEqual(profile["roles"][role]["api_key_env"], "OPENROUTER_API_KEY")


if __name__ == "__main__":
    unittest.main()
