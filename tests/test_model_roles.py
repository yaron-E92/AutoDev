import json
import unittest
from pathlib import Path

from automation.provider_contract import ModelConfig
from automation.provider_mock import MockProvider
from automation.model_roles import ModelInvocationError, invoke_model
from automation.prompt_policies import (
    PROMPT_POLICY_VERSION,
    compose_prompt,
    resolve_prompt_policies,
    safe_prompt_policy_metadata,
)


class ModelRoleTests(unittest.TestCase):





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


    def test_provider_metadata_records_policy_source_and_modes(self):
        policies = resolve_prompt_policies({})
        metadata = safe_prompt_policy_metadata(policies)

        self.assertEqual(metadata["policy_version"], PROMPT_POLICY_VERSION)
        self.assertEqual(metadata["source_version"], "v4.8.4")
        self.assertEqual(metadata["roles"]["verifier"], "review")
        self.assertNotIn("token", json.dumps(metadata).casefold())


if __name__ == "__main__":
    unittest.main()
