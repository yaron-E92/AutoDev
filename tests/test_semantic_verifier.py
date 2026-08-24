import io
import json
import tempfile
import unittest
from pathlib import Path

from automation.provider_contract import ModelConfig, ProviderError
from automation.provider_mock import MockProvider
from automation.prompt_policies import resolve_prompt_policies
from automation.semantic_configuration import resolve_semantic_settings
from automation.semantic_contract import SemanticSettings, SemanticVerifierError
from automation.semantic_evidence import collect_cross_file_regression_evidence
from automation.semantic_invocation import invoke_semantic_verifier
from automation.semantic_prompts import build_semantic_prompt, extract_acceptance_criteria
from automation.semantic_schema import parse_semantic_output


REPO_ROOT = Path(__file__).resolve().parents[1]


def semantic_result(verdict="pass", status="met", severity=None, repair_brief=""):
    findings = []
    if severity:
        findings.append({"severity": severity, "message": "Concrete finding", "path": "src/a.py"})
    return json.dumps(
        {
            "verdict": verdict,
            "requirements": [
                {
                    "criterion": "The requested behavior is implemented",
                    "status": status,
                    "evidence": ["src/a.py", "verification/attempt-0.md"],
                }
            ],
            "findings": findings,
            "repair_brief": repair_brief,
        }
    )


class SemanticVerifierTests(unittest.TestCase):


    def test_extracts_detectable_acceptance_criteria(self):
        issue = """# Issue

## Acceptance criteria

- First requirement
- Second requirement

## Non-goals
- Ignore this
"""

        self.assertEqual(
            extract_acceptance_criteria(issue),
            ["First requirement", "Second requirement"],
        )

    def test_strict_schema_accepts_pass_repair_and_blocked(self):
        passed = parse_semantic_output(semantic_result())
        repair = parse_semantic_output(
            semantic_result("repair", "missing", "blocking", "Fix the missing behavior.")
        )
        blocked = parse_semantic_output(
            semantic_result("blocked", "uncertain", "blocking", "Human evidence is required.")
        )

        self.assertEqual(passed["verdict"], "pass")
        self.assertEqual(repair["verdict"], "repair")
        self.assertEqual(blocked["verdict"], "blocked")

    def test_malformed_or_optimistic_output_never_defaults_to_pass(self):
        with self.assertRaises(SemanticVerifierError):
            parse_semantic_output("PASS")
        with self.assertRaises(SemanticVerifierError):
            parse_semantic_output(semantic_result("pass", "missing"))
        with self.assertRaises(SemanticVerifierError):
            parse_semantic_output(semantic_result("repair", "missing", "blocking", ""))

    def test_settings_default_to_configured_verifier_and_support_disable(self):
        enabled = resolve_semantic_settings({}, verifier_configured=True)
        disabled = resolve_semantic_settings(
            {"semantic_verification": {"enabled": False}},
            verifier_configured=True,
        )

        self.assertTrue(enabled.enabled)
        self.assertEqual(enabled.max_schema_retries, 1)
        self.assertEqual(enabled.max_repair_attempts, 1)
        self.assertFalse(disabled.enabled)
        with self.assertRaises(SemanticVerifierError):
            resolve_semantic_settings(
                {"semantic_verification": {"enabled": True}},
                verifier_configured=False,
            )

    def test_bounded_prompt_contains_requirements_diff_and_deterministic_evidence(self):
        prompt = build_semantic_prompt(
            issue_text="# Issue\n\n## Acceptance criteria\n- Show the value",
            synthesized_handoff="Relevant repository handoff",
            plan="Edit src/a.py",
            changed_files=["src/a.py"],
            diff="diff --git a/src/a.py b/src/a.py",
            deterministic_evidence="dotnet test passed",
            uncertainty_notes="Android build skipped",
        )

        self.assertIn("Show the value", prompt)
        self.assertIn("src/a.py", prompt)
        self.assertIn("diff --git", prompt)
        self.assertIn("dotnet test passed", prompt)
        self.assertIn("Android build skipped", prompt)

    def test_semantic_prompt_rejects_unknown_unresolved_placeholder(self):
        with self.assertRaises(SemanticVerifierError) as raised:
            build_semantic_prompt(
                issue_text="# Issue",
                synthesized_handoff="Handoff",
                plan="Plan",
                changed_files=[],
                diff="",
                deterministic_evidence="checks passed",
                template="Issue: {{IssueText}}\nMissing: {{MissingRequiredEvidence}}\n",
            )

        self.assertEqual(raised.exception.classification, "unresolved_semantic_placeholders")
        self.assertIn("MissingRequiredEvidence", str(raised.exception))

    def test_cross_file_regression_evidence_flags_unchanged_reference_to_removed_symbol(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            src = repo / "src"
            src.mkdir()
            changed = src / "CampaignState.cs"
            unchanged = src / "CampaignViewModel.cs"
            changed.write_text("public class CampaignState { }\n", encoding="utf-8")
            unchanged.write_text(
                "if (campaign.RecoveredStaleActiveCampaign) { Continue(); }\n",
                encoding="utf-8",
            )
            diff = (
                "diff --git a/src/CampaignState.cs b/src/CampaignState.cs\n"
                "--- a/src/CampaignState.cs\n"
                "+++ b/src/CampaignState.cs\n"
                "@@ -1 +1 @@\n"
                "-public bool RecoveredStaleActiveCampaign { get; set; }\n"
                "+public class CampaignState { }\n"
            )

            evidence = collect_cross_file_regression_evidence(
                repo,
                ["src/CampaignState.cs"],
                diff,
            )
            prompt = build_semantic_prompt(
                issue_text="# Issue",
                synthesized_handoff="Handoff",
                plan="Plan",
                changed_files=["src/CampaignState.cs"],
                diff=diff,
                deterministic_evidence="checks passed",
                cross_file_regression_evidence=evidence,
            )

        self.assertIn("RecoveredStaleActiveCampaign", evidence)
        self.assertIn("src/CampaignViewModel.cs:1", evidence)
        self.assertIn("potential blocking regression", evidence)
        self.assertIn("src/CampaignViewModel.cs:1", prompt)

    def test_schema_retry_uses_verifier_again_and_records_separate_telemetry(self):
        provider = MockProvider(["not json", semantic_result()])
        policies = resolve_prompt_policies({})
        with tempfile.TemporaryDirectory() as temp_dir:
            telemetry = Path(temp_dir) / "model-invocations.json"
            result = invoke_semantic_verifier(
                provider=provider,
                config=ModelConfig(provider="mock", model="verifier"),
                prompt="Review this implementation.",
                telemetry_path=telemetry,
                policies=policies,
                max_schema_retries=1,
            )
            records = json.loads(telemetry.read_text(encoding="utf-8"))

        self.assertEqual(result["verdict"], "pass")
        self.assertEqual(len(provider.prompts), 2)
        self.assertIn("previous response was rejected", provider.prompts[1].casefold())
        self.assertEqual([record["role"] for record in records], ["verifier", "verifier"])
        self.assertEqual([record["attempt"] for record in records], [0, 1])



if __name__ == "__main__":
    unittest.main()
