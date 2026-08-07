import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from automation import prompt_runner, run_real_issue
from automation.model_providers import ModelConfig, MockProvider, ProviderError
from automation.prompt_policies import resolve_prompt_policies
from automation.semantic_verifier import (
    SemanticSettings,
    SemanticVerifierError,
    build_semantic_prompt,
    extract_acceptance_criteria,
    invoke_semantic_verifier,
    parse_semantic_output,
    resolve_semantic_settings,
)


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

    def test_prompt_runner_semantic_mode_keeps_legacy_mode_available(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            profile = root / "profile.json"
            prompt = root / "prompt.md"
            semantic_output = root / "semantic.json"
            legacy_output = root / "legacy.txt"
            profile.write_text(
                json.dumps(
                    {
                        "version": 2,
                        "roles": {
                            "verifier": {"transport": "mock", "model": "verifier"}
                        },
                    }
                ),
                encoding="utf-8",
            )
            prompt.write_text("Verify this patch.", encoding="utf-8")

            with mock.patch.object(
                prompt_runner,
                "create_provider",
                side_effect=[MockProvider([semantic_result()]), MockProvider(["PASS\nLooks good."])],
            ):
                semantic_code = prompt_runner.run(
                    [
                        "--role", "verifier",
                        "--provider-profile", str(profile),
                        "--prompt-file", str(prompt),
                        "--output-file", str(semantic_output),
                        "--verifier-format", "semantic-json",
                    ]
                )
                legacy_code = prompt_runner.run(
                    [
                        "--role", "verifier",
                        "--provider-profile", str(profile),
                        "--prompt-file", str(prompt),
                        "--output-file", str(legacy_output),
                    ]
                )

            self.assertEqual(semantic_code, 0)
            self.assertEqual(legacy_code, 0)
            self.assertEqual(json.loads(semantic_output.read_text(encoding="utf-8"))["verdict"], "pass")
            self.assertTrue(legacy_output.read_text(encoding="utf-8").startswith("PASS"))

    def test_operational_gate_uses_independent_verifier_and_writes_final_verdict(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            out_dir = Path(temp_dir)
            (out_dir / "coder-plan.md").write_text("Plan", encoding="utf-8")
            (out_dir / "synthesized-handoff.md").write_text("Handoff", encoding="utf-8")
            (out_dir / "verification-result-summary.md").write_text("Checks passed", encoding="utf-8")
            (out_dir / "recommended-command-groups.json").write_text("{}", encoding="utf-8")
            verifier = MockProvider([semantic_result()])
            roles = {
                "reader": None,
                "synthesizer": None,
                "planner": None,
                "implementer": ModelConfig(provider="mock", model="implementer"),
                "fixer": ModelConfig(provider="mock", model="fixer"),
                "verifier": ModelConfig(provider="mock", model="verifier"),
            }
            verification = run_real_issue.VerificationResult(
                0,
                0,
                "mock",
                "passed",
                "",
                out_dir / "verification" / "attempt-0.md",
            )
            semantic_token = run_real_issue._ACTIVE_SEMANTIC.set(SemanticSettings(True))
            policy_token = run_real_issue._ACTIVE_POLICIES.set(resolve_prompt_policies({}))
            originals = (run_real_issue.collect_changed_files, run_real_issue.collect_current_diff)
            try:
                run_real_issue.collect_changed_files = lambda repo: ["src/a.py"]
                run_real_issue.collect_current_diff = lambda repo, files: "diff --git a/src/a.py b/src/a.py"
                result = run_real_issue.run_semantic_verification_gate(
                    repo=out_dir,
                    out_dir=out_dir,
                    issue_text="# Issue\n\n## Acceptance criteria\n- Implement behavior",
                    verification=verification,
                    roles=roles,
                    fixer_provider=None,
                    fixer_config=roles["fixer"],
                    factory=lambda config: verifier if config.model == "verifier" else MockProvider(),
                    stream=io.StringIO(),
                )
            finally:
                run_real_issue.collect_changed_files, run_real_issue.collect_current_diff = originals
                run_real_issue._ACTIVE_SEMANTIC.reset(semantic_token)
                run_real_issue._ACTIVE_POLICIES.reset(policy_token)

            final = json.loads((out_dir / "verification" / "final-verdict.json").read_text(encoding="utf-8"))

        self.assertTrue(result.passed)
        self.assertEqual(final["verdict"], "pass")
        self.assertEqual(len(verifier.prompts), 1)

    def test_operational_repair_uses_fixer_then_reruns_deterministic_and_semantic_checks(self):
        repair_result = semantic_result("repair", "missing", "blocking", "Change src/a.py only.")
        patch_response = (
            "BEGIN_UNIFIED_DIFF\n"
            "diff --git a/src/a.py b/src/a.py\n"
            "--- a/src/a.py\n"
            "+++ b/src/a.py\n"
            "@@ -1 +1 @@\n"
            "-old\n"
            "+new\n"
            "END_UNIFIED_DIFF"
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            out_dir = Path(temp_dir)
            for name, value in (
                ("coder-plan.md", "Plan"),
                ("synthesized-handoff.md", "Handoff"),
                ("verification-result-summary.md", "Checks passed"),
                ("recommended-command-groups.json", "{}"),
            ):
                (out_dir / name).write_text(value, encoding="utf-8")
            verifier = MockProvider([repair_result, semantic_result()])
            fixer = MockProvider([patch_response])
            roles = {
                "reader": None,
                "synthesizer": None,
                "planner": None,
                "implementer": ModelConfig(provider="mock", model="implementer"),
                "fixer": ModelConfig(provider="mock", model="fixer"),
                "verifier": ModelConfig(provider="mock", model="verifier"),
            }
            verification = run_real_issue.VerificationResult(
                0, 0, "mock", "passed", "", out_dir / "verification" / "attempt-0.md"
            )
            semantic_token = run_real_issue._ACTIVE_SEMANTIC.set(SemanticSettings(True, 1, 1))
            policy_token = run_real_issue._ACTIVE_POLICIES.set(resolve_prompt_policies({}))
            originals = (
                run_real_issue.collect_changed_files,
                run_real_issue.collect_current_diff,
                run_real_issue.apply_patch_file,
                run_real_issue.run_recommended_verification,
                run_real_issue.write_verification_result,
            )
            deterministic_attempts = []
            try:
                run_real_issue.collect_changed_files = lambda repo: ["src/a.py"]
                run_real_issue.collect_current_diff = lambda repo, files: "diff --git a/src/a.py b/src/a.py"
                run_real_issue.apply_patch_file = lambda repo, patch, stream: None
                run_real_issue.run_recommended_verification = lambda out, repo, attempt, stream: (
                    deterministic_attempts.append(attempt)
                    or run_real_issue.VerificationResult(
                        attempt, 0, "mock", "passed", "", out / "verification" / f"attempt-{attempt}.md"
                    )
                )
                run_real_issue.write_verification_result = lambda out, result: None
                result = run_real_issue.run_semantic_verification_gate(
                    repo=out_dir,
                    out_dir=out_dir,
                    issue_text="# Issue\n\n## Acceptance criteria\n- Implement behavior",
                    verification=verification,
                    roles=roles,
                    fixer_provider=fixer,
                    fixer_config=roles["fixer"],
                    factory=lambda config: verifier if config.model == "verifier" else fixer,
                    stream=io.StringIO(),
                )
            finally:
                (
                    run_real_issue.collect_changed_files,
                    run_real_issue.collect_current_diff,
                    run_real_issue.apply_patch_file,
                    run_real_issue.run_recommended_verification,
                    run_real_issue.write_verification_result,
                ) = originals
                run_real_issue._ACTIVE_SEMANTIC.reset(semantic_token)
                run_real_issue._ACTIVE_POLICIES.reset(policy_token)

        self.assertTrue(result.passed)
        self.assertEqual(deterministic_attempts, [1])
        self.assertEqual(len(fixer.prompts), 1)
        self.assertEqual(len(verifier.prompts), 2)

    def test_provider_failure_is_not_reported_as_semantic_blocked(self):
        class FailingProvider(MockProvider):
            def invoke(self, prompt, *, model, timeout_seconds):
                raise ProviderError("unavailable", classification="rate_limited", status_code=429)

        with tempfile.TemporaryDirectory() as temp_dir:
            out_dir = Path(temp_dir)
            (out_dir / "verification-result-summary.md").write_text("Checks passed", encoding="utf-8")
            roles = {
                "reader": None,
                "synthesizer": None,
                "planner": None,
                "implementer": ModelConfig(provider="mock", model="implementer"),
                "fixer": ModelConfig(provider="mock", model="fixer"),
                "verifier": ModelConfig(provider="mock", model="verifier"),
            }
            verification = run_real_issue.VerificationResult(
                0, 0, "mock", "passed", "", out_dir / "verification" / "attempt-0.md"
            )
            semantic_token = run_real_issue._ACTIVE_SEMANTIC.set(SemanticSettings(True))
            policy_token = run_real_issue._ACTIVE_POLICIES.set(resolve_prompt_policies({}))
            originals = (run_real_issue.collect_changed_files, run_real_issue.collect_current_diff)
            try:
                run_real_issue.collect_changed_files = lambda repo: []
                run_real_issue.collect_current_diff = lambda repo, files: ""
                with self.assertRaises(run_real_issue.ModelInvocationError) as raised:
                    run_real_issue.run_semantic_verification_gate(
                        repo=out_dir,
                        out_dir=out_dir,
                        issue_text="# Issue",
                        verification=verification,
                        roles=roles,
                        fixer_provider=None,
                        fixer_config=roles["fixer"],
                        factory=lambda config: FailingProvider(),
                        stream=io.StringIO(),
                    )
            finally:
                run_real_issue.collect_changed_files, run_real_issue.collect_current_diff = originals
                run_real_issue._ACTIVE_SEMANTIC.reset(semantic_token)
                run_real_issue._ACTIVE_POLICIES.reset(policy_token)

        self.assertEqual(raised.exception.record["failure_classification"], "rate_limited")
        self.assertEqual(raised.exception.record["status_code"], 429)

    def test_windows_and_linux_gate_before_pr_and_reverify_after_ci_repair(self):
        windows = (REPO_ROOT / "windows" / "scripts" / "issue-to-pr-cycle.ps1").read_text(encoding="utf-8")
        linux = (REPO_ROOT / "linux" / "scripts" / "issue-to-pr-cycle.sh").read_text(encoding="utf-8")

        self.assertIn('VerifierFormat "semantic-json"', windows)
        self.assertIn("automation.semantic_verifier", windows)
        self.assertLess(windows.index("Invoke-SemanticGate"), windows.index("Invoke-PrAndCiWithRepairs"))
        self.assertIn("$semanticCode = Invoke-SemanticGate", windows)

        self.assertIn("semantic-json", linux)
        self.assertIn("automation.semantic_verifier", linux)
        self.assertLess(linux.index("semantic_gate"), linux.index("pr_and_ci_with_repairs"))
        self.assertIn("semantic_gate || return", linux)


if __name__ == "__main__":
    unittest.main()
