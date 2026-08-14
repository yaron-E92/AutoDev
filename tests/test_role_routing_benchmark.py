from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from automation import eval_harness
from automation import role_routing_benchmark as routing


REPO_ROOT = Path(__file__).resolve().parents[1]
CASES_PATH = REPO_ROOT / "benchmarks" / "eval" / "cases.json"
PROFILES_PATH = REPO_ROOT / "benchmarks" / "eval" / "profiles.json"


class RoleRoutingBenchmarkTests(unittest.TestCase):
    def test_provider_profiles_expose_request_limits_and_privacy_admissibility(self):
        profiles = eval_harness.load_profiles(PROFILES_PATH)

        local_roles = profiles["local-ollama"]["provider_summary"]["benchmark"]["roles"]
        for role in ("planner", "implementer", "fixer", "verifier"):
            self.assertEqual(local_roles[role]["candidate_class"], "local")
            self.assertEqual(local_roles[role]["privacy"]["strict-confidential"]["outcome"], "ALLOW")
            self.assertEqual(local_roles[role]["privacy"]["local-only"]["outcome"], "ALLOW")

        groq_roles = profiles["groq-only"]["provider_summary"]["benchmark"]["roles"]
        planner = groq_roles["planner"]
        self.assertEqual(planner["candidate_class"], "free-cloud")
        self.assertEqual(planner["request_limits"]["context_window_tokens"], 131072)
        self.assertEqual(planner["privacy"]["no-training"]["outcome"], "ALLOW")
        self.assertEqual(planner["privacy"]["strict-confidential"]["outcome"], "CONSENT_REQUIRED")
        self.assertEqual(planner["privacy"]["local-only"]["outcome"], "BLOCK")

        mixed_roles = profiles["mixed-groq-openrouter-semantic"]["provider_summary"]["benchmark"]["roles"]
        implementer = mixed_roles["implementer"]
        self.assertEqual(implementer["candidate_class"], "free-cloud")
        self.assertEqual(implementer["fact_key"], "openrouter/free")
        self.assertEqual(implementer["request_limits"]["free_requests_per_day_default"], 50)
        self.assertEqual(implementer["privacy"]["strict-confidential"]["outcome"], "CONSENT_REQUIRED")
        self.assertIn("provider.zdr=true", implementer["privacy"]["strict-confidential"]["enforcement_controls"])

    def test_replay_report_keeps_unrun_local_and_placeholder_free_candidates_unqualified(self):
        cases = eval_harness.load_cases(CASES_PATH)
        profiles = eval_harness.load_profiles(PROFILES_PATH)
        selected = ("legacy-command", "local-ollama", "mixed-groq-openrouter-semantic")
        results = [
            eval_harness.load_replay(case, profiles[profile], cases_path=CASES_PATH)
            for case in cases.values()
            for profile in selected
        ]

        aggregate = eval_harness.aggregate(results, cases)
        role_candidates = aggregate["role_candidates"]
        coverage = aggregate["benchmark_coverage"]

        local_planner = next(
            candidate
            for candidate in role_candidates["planner"].values()
            if candidate["candidate_class"] == "local"
        )
        self.assertGreater(local_planner["configured_runs"], 0)
        self.assertEqual(local_planner["observed_runs"], 0)

        openrouter_implementer = next(
            candidate
            for candidate in role_candidates["implementer"].values()
            if candidate["fact_key"] == "openrouter/free" if "fact_key" in candidate
        ) if False else next(
            candidate
            for candidate in role_candidates["implementer"].values()
            if candidate["candidate_class"] == "free-cloud" and "REPLACE_WITH" in candidate["candidate_id"]
        )
        self.assertGreater(openrouter_implementer["configured_runs"], 0)
        self.assertEqual(openrouter_implementer["observed_runs"], 0)

        self.assertFalse(coverage["complete"])
        self.assertIn("planner:local", coverage["missing"])
        self.assertIn("implementer:local", coverage["missing"])
        self.assertIn("implementer:free-cloud", coverage["missing"])
        self.assertIn("fixer:local", coverage["missing"])
        self.assertIn("fixer:free-cloud", coverage["missing"])
        self.assertIn("verifier:local", coverage["missing"])

        self.assertEqual(
            aggregate["routing_recommendation"]["roles"]["implementer"]["candidate_class"],
            "frontier-baseline",
        )
        self.assertEqual(
            aggregate["routing_recommendation"]["roles"]["fixer"]["candidate_class"],
            "frontier-baseline",
        )

    def test_privacy_filter_precedes_cost_class_when_recommending(self):
        candidates = {
            "local": {
                "candidate_id": "local/model",
                "candidate_class": "local",
                "model": "local-model",
                "profiles": ["local"],
                "observed_runs": 1,
                "completed_workflows": 1,
                "deterministic_passes": 1,
                "semantic_passes": 1,
                "provider_failures": 0,
                "downstream_repairs": 1,
                "workflow_tokens_known": 0,
                "workflow_total_tokens": 0,
                "workflow_wall_time_known": 0,
                "workflow_wall_time_ms": 0,
                "privacy": {"strict-confidential": {"outcome": "ALLOW"}},
            },
            "free": {
                "candidate_id": "free/model",
                "candidate_class": "free-cloud",
                "model": "free-model",
                "profiles": ["free"],
                "observed_runs": 1,
                "completed_workflows": 1,
                "deterministic_passes": 1,
                "semantic_passes": 1,
                "provider_failures": 0,
                "downstream_repairs": 0,
                "workflow_tokens_known": 0,
                "workflow_total_tokens": 0,
                "workflow_wall_time_known": 0,
                "workflow_wall_time_ms": 0,
                "privacy": {"strict-confidential": {"outcome": "CONSENT_REQUIRED"}},
            },
        }

        recommendation = routing.recommend_role("planner", candidates)

        self.assertEqual(recommendation["recommended_candidate"], "local/model")
        self.assertEqual(recommendation["status"], "qualified-by-observed-workflow-evidence")

    def test_zero_repairs_are_ranked_as_zero_not_as_missing(self):
        candidates = {
            "one-repair": {
                "candidate_id": "one-repair",
                "candidate_class": "local",
                "model": "a",
                "profiles": ["a"],
                "observed_runs": 1,
                "completed_workflows": 1,
                "deterministic_passes": 1,
                "semantic_passes": 1,
                "provider_failures": 0,
                "downstream_repairs": 1,
                "workflow_tokens_known": 0,
                "workflow_total_tokens": 0,
                "workflow_wall_time_known": 0,
                "workflow_wall_time_ms": 0,
                "privacy": {"strict-confidential": {"outcome": "ALLOW"}},
            },
            "zero-repair": {
                "candidate_id": "zero-repair",
                "candidate_class": "free-cloud",
                "model": "b",
                "profiles": ["b"],
                "observed_runs": 1,
                "completed_workflows": 1,
                "deterministic_passes": 1,
                "semantic_passes": 1,
                "provider_failures": 0,
                "downstream_repairs": 0,
                "workflow_tokens_known": 0,
                "workflow_total_tokens": 0,
                "workflow_wall_time_known": 0,
                "workflow_wall_time_ms": 0,
                "privacy": {"strict-confidential": {"outcome": "ALLOW"}},
            },
        }

        recommendation = routing.recommend_role("implementer", candidates)

        self.assertEqual(recommendation["recommended_candidate"], "zero-repair")

    def test_provider_fact_snapshot_is_versioned_source_backed_and_secret_free(self):
        facts = routing.load_provider_facts(REPO_ROOT / "benchmarks" / "eval" / "provider-facts.json")
        rendered = json.dumps(facts)

        self.assertEqual(facts["schema_version"], 1)
        self.assertEqual(facts["checked_at"], "2026-08-14")
        self.assertTrue(facts["facts"]["groq/openai-gpt-oss-20b"]["source"])
        self.assertTrue(facts["facts"]["openrouter/free"]["source"])
        self.assertNotIn("api_key", rendered.casefold())
        self.assertNotIn("authorization", rendered.casefold())
        self.assertNotIn("bearer ", rendered.casefold())

    def test_routing_outputs_are_written_separately_from_base_comparison(self):
        cases = eval_harness.load_cases(CASES_PATH)
        profiles = eval_harness.load_profiles(PROFILES_PATH)
        results = [
            eval_harness.load_replay(case, profiles[profile], cases_path=CASES_PATH)
            for case in cases.values()
            for profile in ("legacy-command", "local-ollama")
        ]
        aggregate = eval_harness.aggregate(results, cases)

        with tempfile.TemporaryDirectory() as temp_dir:
            out = Path(temp_dir)
            eval_harness.write_results(out, results, aggregate)
            recommendation = json.loads((out / "routing-recommendation.json").read_text(encoding="utf-8"))
            markdown = (out / "routing-recommendation.md").read_text(encoding="utf-8")

        self.assertIn("benchmark_coverage", recommendation)
        self.assertIn("routing_recommendation", recommendation)
        self.assertIn("Routing recommendation", markdown)
        self.assertIn("Acceptance-evidence gap", markdown)


if __name__ == "__main__":
    unittest.main()
