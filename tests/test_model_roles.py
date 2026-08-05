import io
import json
import tempfile
import unittest
from pathlib import Path

from area_reader_v2 import runner as area_runner
from automation import run_real_issue
from automation.model_providers import ModelConfig, MockProvider, ProviderError
from automation.model_roles import ModelInvocationError, invoke_model, resolve_role_configs
from automation.prompt_policies import (
    PROMPT_POLICY_VERSION,
    compose_prompt,
    resolve_prompt_policies,
    safe_prompt_policy_metadata,
)


class ModelRoleTests(unittest.TestCase):
    def setUp(self):
        self.defaults = {
            "reader": {"provider": "mock", "model": "reader-default"},
            "coder": {"provider": "mock", "model": "coder-default"},
        }

    def test_version_two_roles_are_independent(self):
        roles = resolve_role_configs(
            defaults=self.defaults,
            file_config={
                "version": 2,
                "roles": {
                    role: {"provider": "mock", "model": role}
                    for role in ("reader", "synthesizer", "planner", "implementer", "fixer", "verifier")
                },
            },
        )
        self.assertEqual([roles[role].model for role in roles], [
            "reader", "synthesizer", "planner", "implementer", "fixer", "verifier"
        ])

    def test_legacy_reader_coder_fallbacks_and_disabled_verifier(self):
        roles = resolve_role_configs(
            defaults=self.defaults,
            file_config={
                "reader": {"provider": "mock", "model": "legacy-reader"},
                "coder": {"provider": "mock", "model": "legacy-coder"},
            },
        )
        self.assertEqual(roles["reader"].model, "legacy-reader")
        self.assertEqual(roles["synthesizer"].model, "legacy-reader")
        self.assertEqual(roles["planner"].model, "legacy-coder")
        self.assertEqual(roles["implementer"].model, "legacy-coder")
        self.assertEqual(roles["fixer"].model, "legacy-coder")
        self.assertIsNone(roles["verifier"])

    def test_explicit_role_wins_over_legacy_cli_override(self):
        roles = resolve_role_configs(
            defaults=self.defaults,
            file_config={
                "version": 2,
                "roles": {"planner": {"provider": "mock", "model": "explicit-planner"}},
            },
            cli_values={"coder": {"model": "cli-coder"}},
        )
        self.assertEqual(roles["planner"].model, "explicit-planner")
        self.assertEqual(roles["implementer"].model, "cli-coder")
        self.assertEqual(roles["fixer"].model, "cli-coder")

    def test_unknown_config_version_is_rejected(self):
        with self.assertRaises(ProviderError):
            resolve_role_configs(defaults=self.defaults, file_config={"version": 3})

    def test_failed_call_has_safe_role_metadata(self):
        class FailingProvider(MockProvider):
            def generate(self, prompt, *, model, timeout_seconds):
                raise RuntimeError("secret failure detail")

        with self.assertRaises(ModelInvocationError) as raised:
            invoke_model(
                FailingProvider(),
                ModelConfig(provider="mock", model="m"),
                "prompt",
                role="planner",
            )
        self.assertEqual(raised.exception.record["role"], "planner")
        self.assertEqual(raised.exception.record["status"], "failure")
        self.assertNotIn("secret failure detail", json.dumps(raised.exception.record))

    def test_prompt_policy_defaults_match_roles(self):
        policies = resolve_prompt_policies({})

        self.assertEqual(
            policies,
            {
                "reader": "off",
                "synthesizer": "lite",
                "planner": "lite",
                "implementer": "full",
                "fixer": "full",
                "verifier": "review",
            },
        )

    def test_prompt_policies_support_global_disable_and_role_overrides(self):
        disabled = resolve_prompt_policies({"prompt_policy": {"enabled": False}})
        overridden = resolve_prompt_policies(
            {
                "prompt_policy": {
                    "roles": {
                        "planner": "off",
                        "implementer": "lite",
                    }
                }
            }
        )

        self.assertEqual(set(disabled.values()), {"off"})
        self.assertEqual(overridden["planner"], "off")
        self.assertEqual(overridden["implementer"], "lite")
        self.assertEqual(overridden["fixer"], "full")

    def test_reader_policy_does_not_inject_minimization_guidance(self):
        prompt = "Reader role contract.\n\nOriginal issue:\nInspect the repository.\n"

        effective = compose_prompt("reader", prompt, "off")

        self.assertEqual(effective, prompt)
        self.assertNotIn("smallest", effective.casefold())
        self.assertNotIn("reuse", effective.casefold())
        self.assertNotIn("deletion", effective.casefold())

    def test_lite_full_and_review_policies_match_role_intent(self):
        prompt = "Role contract.\n\nOriginal issue:\nIssue text.\n\nOutput contract:\nJSON only.\n"

        lite = compose_prompt("planner", prompt, "lite")
        full = compose_prompt("implementer", prompt, "full")
        fixer = compose_prompt("fixer", prompt, "full")
        review = compose_prompt("verifier", prompt, "review")

        self.assertIn("smallest complete approach", lite)
        self.assertIn("Reuse existing code", full)
        self.assertIn("trust-boundary validation", full)
        self.assertIn("Reuse existing code", fixer)
        self.assertIn("Review only; do not implement or rewrite", review)
        self.assertIn("simplified away", review)
        self.assertNotIn("Reuse existing code", review)

    def test_policy_injection_preserves_terminal_output_contract(self):
        contract = (
            "Patch response contract:\n"
            "BEGIN_UNIFIED_DIFF\n"
            "<unified git diff>\n"
            "END_UNIFIED_DIFF\n\n"
            "No-change response contract:\n"
            "NO_CHANGES_REQUIRED\n"
            "<short explanation>\n"
        )
        prompt = "Implementer role contract.\n\nIssue:\nIssue text.\n\n" + contract

        effective = compose_prompt("implementer", prompt, "full")

        self.assertTrue(effective.endswith(contract))
        self.assertLess(effective.index("Role-specific prompt policy"), effective.index("Issue:"))

    def test_area_runner_records_reader_synthesizer_planner_order(self):
        configs = {
            role: ModelConfig(provider="mock", model=role)
            for role in ("reader", "synthesizer", "planner")
        }
        original_factory = area_runner.create_provider
        original_policies = area_runner._ACTIVE_POLICIES
        with tempfile.TemporaryDirectory() as temp_dir:
            area_runner._ACTIVE_CONFIGS = {**configs, "implementer": None, "fixer": None, "verifier": None}
            area_runner._ACTIVE_POLICIES = resolve_prompt_policies({})
            area_runner._ACTIVE_OUT = Path(temp_dir)
            try:
                area_runner.create_provider = lambda config: MockProvider([config.model])
                area_runner.call_provider(None, "reader", "area", 1)
                area_runner.call_provider(None, "reader", "synthesis", 1, model_override="legacy-alias")
                area_runner.call_provider(None, "coder", "plan", 1)
            finally:
                area_runner.create_provider = original_factory
                area_runner._ACTIVE_POLICIES = original_policies
            records = json.loads((Path(temp_dir) / "model-invocations.json").read_text(encoding="utf-8"))
        self.assertEqual([record["role"] for record in records], ["reader", "synthesizer", "planner"])
        self.assertEqual([record["prompt_policy_mode"] for record in records], ["off", "lite", "lite"])

    def test_dry_run_routes_initial_patch_to_implementer_only(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            out_dir = Path(temp_dir)
            (out_dir / "synthesized-handoff.md").write_text("handoff", encoding="utf-8")
            (out_dir / "coder-plan.md").write_text("plan", encoding="utf-8")
            (out_dir / "recommended-command-groups.json").write_text("{}", encoding="utf-8")
            provider = MockProvider([
                "BEGIN_UNIFIED_DIFF\ndiff --git a/a.txt b/a.txt\n--- a/a.txt\n+++ b/a.txt\n@@ -1 +1 @@\n-a\n+b\nEND_UNIFIED_DIFF"
            ])
            policy_token = run_real_issue._ACTIVE_POLICIES.set(resolve_prompt_policies({}))
            try:
                result = run_real_issue.run_implementation_loop(
                    repo=out_dir,
                    out_dir=out_dir,
                    issue_text="Issue",
                    branch_name="autodev/issue-34",
                    implementer_provider=provider,
                    implementer_config=ModelConfig(provider="mock", model="implementer"),
                    fixer_config=ModelConfig(provider="mock", model="fixer"),
                    max_fix_attempts=1,
                    dry_run=True,
                    stream=io.StringIO(),
                )
            finally:
                run_real_issue._ACTIVE_POLICIES.reset(policy_token)
            records = json.loads((out_dir / "model-invocations.json").read_text(encoding="utf-8"))
            implementation_prompt = (out_dir / "implementation-prompt.md").read_text(encoding="utf-8")
        self.assertTrue(result.passed)
        self.assertEqual([record["role"] for record in records], ["implementer"])
        self.assertEqual(records[0]["prompt_policy_mode"], "full")
        self.assertEqual(records[0]["prompt_policy_version"], PROMPT_POLICY_VERSION)
        self.assertIn("Reuse existing code", implementation_prompt)
        self.assertTrue(implementation_prompt.endswith("NO_CHANGES_REQUIRED\n<short explanation>\n"))

    def test_provider_metadata_records_policy_source_and_modes(self):
        policies = resolve_prompt_policies({})
        metadata = safe_prompt_policy_metadata(policies)

        self.assertEqual(metadata["policy_version"], PROMPT_POLICY_VERSION)
        self.assertEqual(metadata["source_version"], "v4.8.4")
        self.assertEqual(metadata["roles"]["verifier"], "review")
        self.assertNotIn("token", json.dumps(metadata).casefold())


if __name__ == "__main__":
    unittest.main()
