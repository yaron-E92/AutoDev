import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from automation import prompt_runner
from automation.model_providers import ModelProvider, ProviderResponse
from automation.provider_preflight import run_preflight


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

    def test_preflight_reports_missing_credentials_without_network_access(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            profile = Path(temp_dir) / "profile.json"
            profile.write_text(
                json.dumps(
                    {
                        "version": 2,
                        "roles": {
                            "reader": {
                                "transport": "openai-compatible-chat-completions",
                                "model": "m",
                                "base_url": "https://example.invalid/v1",
                                "api_key_env": "MISSING_AUTODEV_KEY",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("MISSING_AUTODEV_KEY", None)
                result = run_preflight(profile, urlopen=mock.Mock(side_effect=AssertionError("network used")))

        reader = next(item for item in result["checks"] if item["role"] == "reader")
        self.assertEqual(reader["failure_classification"], "missing_credentials")
        self.assertNotIn("Authorization", json.dumps(result))

    def test_preflight_reports_missing_command_executable(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            profile = Path(temp_dir) / "profile.json"
            profile.write_text(
                json.dumps(
                    {
                        "version": 2,
                        "roles": {
                            "reader": {
                                "transport": "command",
                                "model": "unused",
                                "command": "missing-autodev-command {prompt}",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            result = run_preflight(profile, which=lambda executable: None)

        reader = next(item for item in result["checks"] if item["role"] == "reader")
        self.assertEqual(reader["failure_classification"], "command_unavailable")

    def test_mixed_profile_keeps_openrouter_roles_free_only(self):
        profile = json.loads(
            (REPO_ROOT / "examples" / "providers" / "groq-openrouter-free.json").read_text(encoding="utf-8")
        )

        self.assertEqual(profile["roles"]["planner"]["api_key_env"], "GROQ_API_KEY")
        for role in ("implementer", "fixer"):
            self.assertTrue(profile["roles"][role]["model"].endswith(":free"))
            self.assertTrue(profile["roles"][role]["free_only"])
            self.assertEqual(profile["roles"][role]["api_key_env"], "OPENROUTER_API_KEY")

    def test_windows_and_linux_delegate_profile_roles_to_python(self):
        windows = (REPO_ROOT / "windows" / "scripts" / "issue-to-pr-cycle.ps1").read_text(encoding="utf-8")
        linux = (REPO_ROOT / "linux" / "scripts" / "issue-to-pr-cycle.sh").read_text(encoding="utf-8")

        for script in (windows, linux):
            self.assertIn("provider-profile", script.casefold())
            self.assertIn("automation.prompt_runner", script)
            self.assertIn("automation.provider_preflight", script)
            self.assertNotIn("must be command or ollama", script.casefold())
        self.assertIn('Role "fixer"', windows)
        self.assertIn("run_provider_prompt fixer", linux)
        self.assertIn("PlannerProviderMode", windows)
        self.assertIn("AgentProviderMode", windows)
        self.assertIn("planner_provider_mode", linux)
        self.assertIn("agent_provider_mode", linux)

    def test_windows_and_linux_prepare_forward_the_provider_profile(self):
        windows_prepare = (
            REPO_ROOT / "windows" / "scripts" / "codex-prepare-next-ready-issue.ps1"
        ).read_text(encoding="utf-8")
        linux_prepare = (
            REPO_ROOT / "linux" / "scripts" / "prepare-next-ready-issue.sh"
        ).read_text(encoding="utf-8")

        self.assertIn("prepare_planner_prompt.py", windows_prepare)
        self.assertIn("ProviderProfile", windows_prepare)
        self.assertIn("--provider-profile", linux_prepare)
        self.assertIn("prepare_planner_prompt.py", linux_prepare)


if __name__ == "__main__":
    unittest.main()
