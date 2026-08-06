import io
import json
import tempfile
import unittest
from pathlib import Path

from automation import run_real_issue
from automation.model_providers import ModelConfig, MockProvider
from automation.prompt_policies import resolve_prompt_policies
from automation.semantic_verifier import SemanticSettings


def semantic_result(verdict, status, repair_brief=""):
    severity = "blocking" if verdict != "pass" else "warning"
    return json.dumps(
        {
            "verdict": verdict,
            "requirements": [
                {
                    "criterion": "The requested behavior is implemented",
                    "status": status,
                    "evidence": ["src/a.py"],
                }
            ],
            "findings": [
                {
                    "severity": severity,
                    "message": "Concrete semantic finding",
                    "path": "src/a.py",
                }
            ] if verdict != "pass" else [],
            "repair_brief": repair_brief,
        }
    )


class SemanticVerifierFailureTests(unittest.TestCase):
    def setUp(self):
        self.roles = {
            "reader": None,
            "synthesizer": None,
            "planner": None,
            "implementer": ModelConfig(provider="mock", model="implementer"),
            "fixer": ModelConfig(provider="mock", model="fixer"),
            "verifier": ModelConfig(provider="mock", model="verifier"),
        }

    def _prepare_out_dir(self, root):
        for name, value in (
            ("coder-plan.md", "Plan"),
            ("synthesized-handoff.md", "Handoff"),
            ("verification-result-summary.md", "Checks passed"),
            ("recommended-command-groups.json", "{}"),
        ):
            (root / name).write_text(value, encoding="utf-8")

    def _verification(self, root):
        return run_real_issue.VerificationResult(
            0,
            0,
            "mock",
            "passed",
            "",
            root / "verification" / "attempt-0.md",
        )

    def _set_context(self):
        semantic_token = run_real_issue._ACTIVE_SEMANTIC.set(
            SemanticSettings(True, 1, 1)
        )
        policy_token = run_real_issue._ACTIVE_POLICIES.set(
            resolve_prompt_policies({})
        )
        return semantic_token, policy_token

    def _mock_evidence(self):
        originals = (
            run_real_issue.collect_changed_files,
            run_real_issue.collect_current_diff,
        )
        run_real_issue.collect_changed_files = lambda repo: ["src/a.py"]
        run_real_issue.collect_current_diff = (
            lambda repo, files: "diff --git a/src/a.py b/src/a.py"
        )
        return originals

    def test_blocked_verdict_stops_before_repair_and_preserves_final_artifact(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            out_dir = Path(temp_dir)
            self._prepare_out_dir(out_dir)
            verifier = MockProvider(
                [semantic_result("blocked", "uncertain", "Human evidence is required.")]
            )
            fixer = MockProvider()
            semantic_token, policy_token = self._set_context()
            originals = self._mock_evidence()
            try:
                with self.assertRaises(run_real_issue.RunnerError):
                    run_real_issue.run_semantic_verification_gate(
                        repo=out_dir,
                        out_dir=out_dir,
                        issue_text="# Issue",
                        verification=self._verification(out_dir),
                        roles=self.roles,
                        fixer_provider=fixer,
                        fixer_config=self.roles["fixer"],
                        factory=lambda config: verifier,
                        stream=io.StringIO(),
                    )
            finally:
                (
                    run_real_issue.collect_changed_files,
                    run_real_issue.collect_current_diff,
                ) = originals
                run_real_issue._ACTIVE_SEMANTIC.reset(semantic_token)
                run_real_issue._ACTIVE_POLICIES.reset(policy_token)

            final = json.loads(
                (out_dir / "verification" / "final-verdict.json").read_text(
                    encoding="utf-8"
                )
            )

        self.assertEqual(final["verdict"], "blocked")
        self.assertEqual(len(verifier.prompts), 1)
        self.assertEqual(len(fixer.prompts), 0)

    def test_semantic_repair_failure_blocks_after_final_verifier_attempt(self):
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
        verifier = MockProvider(
            [
                semantic_result("repair", "missing", "Change src/a.py only."),
                semantic_result("blocked", "missing", "Repair did not satisfy the issue."),
            ]
        )
        fixer = MockProvider([patch_response])

        with tempfile.TemporaryDirectory() as temp_dir:
            out_dir = Path(temp_dir)
            self._prepare_out_dir(out_dir)
            semantic_token, policy_token = self._set_context()
            evidence_originals = self._mock_evidence()
            originals = (
                run_real_issue.apply_patch_file,
                run_real_issue.run_recommended_verification,
                run_real_issue.write_verification_result,
            )
            try:
                run_real_issue.apply_patch_file = lambda repo, patch, stream: None
                run_real_issue.run_recommended_verification = (
                    lambda out, repo, attempt, stream: run_real_issue.VerificationResult(
                        attempt,
                        0,
                        "mock",
                        "passed",
                        "",
                        out / "verification" / f"attempt-{attempt}.md",
                    )
                )
                run_real_issue.write_verification_result = lambda out, result: None
                with self.assertRaises(run_real_issue.RunnerError):
                    run_real_issue.run_semantic_verification_gate(
                        repo=out_dir,
                        out_dir=out_dir,
                        issue_text="# Issue",
                        verification=self._verification(out_dir),
                        roles=self.roles,
                        fixer_provider=fixer,
                        fixer_config=self.roles["fixer"],
                        factory=lambda config: verifier if config.model == "verifier" else fixer,
                        stream=io.StringIO(),
                    )
            finally:
                (
                    run_real_issue.collect_changed_files,
                    run_real_issue.collect_current_diff,
                ) = evidence_originals
                (
                    run_real_issue.apply_patch_file,
                    run_real_issue.run_recommended_verification,
                    run_real_issue.write_verification_result,
                ) = originals
                run_real_issue._ACTIVE_SEMANTIC.reset(semantic_token)
                run_real_issue._ACTIVE_POLICIES.reset(policy_token)

            final = json.loads(
                (out_dir / "verification" / "final-verdict.json").read_text(
                    encoding="utf-8"
                )
            )

        self.assertEqual(final["verdict"], "blocked")
        self.assertEqual(len(fixer.prompts), 1)
        self.assertEqual(len(verifier.prompts), 2)


if __name__ == "__main__":
    unittest.main()
