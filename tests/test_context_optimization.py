from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from automation import context_optimization


class ContextOptimizationTests(unittest.TestCase):
    def _repo(self) -> tuple[tempfile.TemporaryDirectory[str], Path, Path]:
        temp = tempfile.TemporaryDirectory()
        repo = Path(temp.name)
        current = repo / ".autodev-run" / "current"
        current.mkdir(parents=True)
        (current / "state.json").write_text(
            json.dumps({"IssueNumber": 101, "ProviderProfile": ""}),
            encoding="utf-8",
        )
        return temp, repo, current

    def test_provider_neutral_token_estimate_is_stable(self) -> None:
        self.assertEqual(context_optimization.approximate_tokens(0), 0)
        self.assertEqual(context_optimization.approximate_tokens(1), 1)
        self.assertEqual(context_optimization.approximate_tokens(8), 2)
        self.assertEqual(context_optimization.approximate_tokens(9), 3)

    def test_planner_replaces_monolithic_prompt_with_durable_evidence_manifest(self) -> None:
        temp, repo, current = self._repo()
        with temp:
            issue = "Issue requirements " + ("A" * 4000)
            handoff = "Repository evidence " + ("B" * 5000)
            baseline = issue + "\n" + handoff + "\n" + ("duplicate plan " * 500)
            (current / "planner.md").write_text(baseline, encoding="utf-8")
            (current / "issue.md").write_text(issue, encoding="utf-8")
            (current / "synthesized-handoff.md").write_text(handoff, encoding="utf-8")
            (current / "detected-facts.json").write_text("{}\n", encoding="utf-8")
            (current / "workspace-snapshot.json").write_text("{}\n", encoding="utf-8")
            (current / "recommended-command-groups.json").write_text("{}\n", encoding="utf-8")
            (current / "coder-plan.md").write_text("prior plan\n", encoding="utf-8")

            context_optimization.optimize_prepared_role(repo, "planner")

            optimized = (current / "planner.md").read_text(encoding="utf-8")
            self.assertIn(".autodev-run/current/issue.md", optimized)
            self.assertIn(".autodev-run/current/synthesized-handoff.md", optimized)
            self.assertIn("normally DO NOT read", optimized)
            self.assertNotIn("A" * 100, optimized)
            self.assertNotIn("B" * 100, optimized)

            profiles = context_optimization.latest_profiles(repo)
            planner = profiles["planner"]
            self.assertEqual(planner["baseline"]["characters"], len(baseline))
            self.assertLess(
                planner["optimized_control"]["effective_characters"],
                planner["baseline"]["characters"],
            )
            components = planner["evidence"]["components"]
            by_name = {Path(item["artifact"]).name: item for item in components}
            self.assertTrue(by_name["issue.md"]["required"])
            self.assertFalse(by_name["coder-plan.md"]["required"])

    def test_fixer_references_targeted_repair_instead_of_copying_it(self) -> None:
        temp, repo, current = self._repo()
        with temp:
            repair = "TARGETED-REPAIR-DETAIL " * 300
            (current / "fixer.md").write_text(repair + "\nboilerplate", encoding="utf-8")
            (current / "local-repair.md").write_text(repair, encoding="utf-8")

            context_optimization.optimize_prepared_role(repo, "fixer", arguments="local")

            optimized = (current / "fixer.md").read_text(encoding="utf-8")
            self.assertIn(".autodev-run/current/local-repair.md", optimized)
            self.assertNotIn("TARGETED-REPAIR-DETAIL TARGETED-REPAIR-DETAIL", optimized)
            fixer = context_optimization.latest_profiles(repo)["fixer"]
            self.assertEqual(fixer["evidence"]["components"][0]["purpose"], "targeted repair instructions")

    def test_verifier_control_prompt_keeps_exact_evidence_out_of_control_text(self) -> None:
        prompt = context_optimization._verifier_prompt()
        self.assertIn("verification-evidence.json", prompt)
        self.assertIn("verification-diff.patch", prompt)
        self.assertIn("verification-result.template.json", prompt)
        self.assertIn("only when needed", prompt)
        self.assertNotIn("Current implementation diff or summary:", prompt)

    def test_report_uses_latest_profile_for_each_role(self) -> None:
        temp, repo, current = self._repo()
        with temp:
            rows = [
                {
                    "role": "planner",
                    "baseline": {"approx_tokens": 100},
                    "optimized_control": {"effective_approx_tokens": 20},
                    "projection": {
                        "required_context_upper_bound_approx_tokens": 60,
                        "initial_prompt_reduction_ratio": 0.8,
                    },
                    "ponytail_prompt_policy": {"mode": "lite"},
                },
                {
                    "role": "planner",
                    "baseline": {"approx_tokens": 80},
                    "optimized_control": {"effective_approx_tokens": 18},
                    "projection": {
                        "required_context_upper_bound_approx_tokens": 50,
                        "initial_prompt_reduction_ratio": 0.775,
                    },
                    "ponytail_prompt_policy": {"mode": "lite"},
                },
            ]
            (current / context_optimization.PROFILE_FILE).write_text(
                "\n".join(json.dumps(row) for row in rows) + "\n",
                encoding="utf-8",
            )
            report = context_optimization.render_report(repo)
            self.assertIn("planner", report)
            self.assertIn("80t", report)
            self.assertNotIn("100t", report)


if __name__ == "__main__":
    unittest.main()
