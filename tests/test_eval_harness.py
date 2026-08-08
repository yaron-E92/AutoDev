from __future__ import annotations

import json
import unittest
from pathlib import Path
from types import SimpleNamespace

from automation import eval_harness


REPO_ROOT = Path(__file__).resolve().parents[1]


class EvalHarnessTests(unittest.TestCase):
    def test_versioned_manifests_parse_and_profiles_reference_operational_configs(self):
        cases = eval_harness.load_cases(REPO_ROOT / "benchmarks" / "eval" / "cases.json")
        profiles = eval_harness.load_profiles(REPO_ROOT / "benchmarks" / "eval" / "profiles.json")

        self.assertEqual(len(cases), 3)
        self.assertIn("docs-minimal-change", cases)
        self.assertIn("python-targeted-repair", cases)
        self.assertIn("no-change-correct", cases)
        for name in (
            "legacy-command",
            "local-ollama",
            "ollama-cloud-nemotron-minimax",
            "groq-only",
            "mixed-groq-openrouter",
            "mixed-groq-openrouter-ponytail",
            "mixed-groq-openrouter-semantic",
            "mixed-groq-openrouter-semantic-headroom",
        ):
            self.assertIn(name, profiles)
            self.assertTrue(Path(profiles[name]["provider_path"]).is_file())

        mixed_paths = {
            profiles[name]["provider_path"]
            for name in (
                "mixed-groq-openrouter",
                "mixed-groq-openrouter-ponytail",
                "mixed-groq-openrouter-semantic",
                "mixed-groq-openrouter-semantic-headroom",
            )
        }
        self.assertEqual(len(mixed_paths), 4)

    def test_two_profiles_compare_all_replay_cases_without_model_calls(self):
        cases_path = REPO_ROOT / "benchmarks" / "eval" / "cases.json"
        cases = eval_harness.load_cases(cases_path)
        profiles = eval_harness.load_profiles(REPO_ROOT / "benchmarks" / "eval" / "profiles.json")

        results = []
        for case in cases.values():
            for name in ("legacy-command", "mixed-groq-openrouter-semantic"):
                results.append(eval_harness.load_replay(case, profiles[name], cases_path=cases_path))

        self.assertEqual(len(results), 6)
        self.assertTrue(all(result["status"] == "completed" for result in results))
        self.assertTrue(all(result["comparable"] for result in results))
        docs = next(
            result
            for result in results
            if result["case_id"] == "docs-minimal-change" and result["profile"] == "legacy-command"
        )
        self.assertEqual(docs["minimality"]["paths"], ["docs/guide.md"])
        self.assertTrue(docs["outcome"]["deterministic_verification_pass"])
        self.assertEqual(docs["outcome"]["semantic"]["verdict"], "pass")

    def test_repair_and_no_change_metrics_are_deterministic(self):
        cases_path = REPO_ROOT / "benchmarks" / "eval" / "cases.json"
        cases = eval_harness.load_cases(cases_path)
        profiles = eval_harness.load_profiles(REPO_ROOT / "benchmarks" / "eval" / "profiles.json")

        repaired = eval_harness.load_replay(
            cases["python-targeted-repair"],
            profiles["legacy-command"],
            cases_path=cases_path,
        )
        no_change = eval_harness.load_replay(
            cases["no-change-correct"],
            profiles["mixed-groq-openrouter-semantic"],
            cases_path=cases_path,
        )

        self.assertEqual(repaired["reliability"]["deterministic_repair_count"], 1)
        self.assertFalse(repaired["reliability"]["first_pass_success"])
        self.assertTrue(no_change["outcome"]["no_change_correct"])
        self.assertEqual(no_change["minimality"]["files_changed"], 0)

    def test_free_openrouter_route_requires_free_only_and_no_paid_fallback(self):
        unsafe = {
            "roles": {
                "implementer": {
                    "transport": "openai-compatible-chat-completions",
                    "model": "vendor/model:free",
                    "base_url": "https://openrouter.ai/api/v1",
                    "free_only": False,
                }
            }
        }
        summary = eval_harness.safe_provider_summary(unsafe)
        with self.assertRaises(eval_harness.EvalError):
            eval_harness.ensure_free_route_safety("unsafe", summary)

        paid_fallback = {
            "roles": {
                "implementer": {
                    "transport": "openai-compatible-chat-completions",
                    "model": "vendor/model:free",
                    "base_url": "https://openrouter.ai/api/v1",
                    "free_only": True,
                    "fallbacks": ["vendor/paid-model"],
                }
            }
        }
        with self.assertRaises(eval_harness.EvalError):
            eval_harness.ensure_free_route_safety(
                "unsafe-fallback",
                eval_harness.safe_provider_summary(paid_fallback),
            )

    def test_free_model_provider_failure_is_first_class_without_substitution(self):
        case = {
            "id": "free-failure",
            "version": 1,
            "base_commit": "base",
            "issue_text": "test",
            "expected": {"changed_paths": [], "forbidden_paths": [], "no_change": False},
        }
        profile = {
            "name": "free-profile",
            "provider_config": "free.json",
            "fingerprint": "fingerprint",
            "provider_summary": {
                "roles": {
                    "implementer": {
                        "transport": "openai-compatible-chat-completions",
                        "model": "vendor/model:free",
                        "endpoint": "https://openrouter.ai/api/v1",
                        "free_only": True,
                    }
                },
                "prompt_policy": {},
                "headroom": {},
            },
        }
        manifest = {
            "schema_version": 1,
            "target": {"base_sha": "base"},
            "completed_stages": ["plan-created"],
            "stages": {},
            "invocations": [
                {
                    "role": "implementer",
                    "transport": "openai-compatible-chat-completions",
                    "model": "vendor/model:free",
                    "status": "failure",
                    "failure_classification": "provider_unavailable",
                    "status_code": 404,
                }
            ],
            "failure": {"classification": "provider_unavailable", "reason": "free model unavailable"},
            "prompt_policy": {},
        }

        result = eval_harness.score_record(
            case,
            profile,
            manifest=manifest,
            semantic={},
            diff_text="",
            diagnostics={},
            replay_meta={},
            mode="replay",
        )

        self.assertEqual(result["status"], "unavailable/provider-failed")
        self.assertTrue(result["reliability"]["free_model_unavailable_without_paid_substitution"])
        self.assertEqual(result["reliability"]["provider_failures"][0]["model"], "vendor/model:free")

    def test_live_safety_budgets_fail_before_execution(self):
        args = SimpleNamespace(
            max_cases=1,
            max_model_calls=1,
            timeout_seconds=10,
            max_reported_cost=None,
            sandbox_pr=False,
            live=True,
            apply=True,
        )
        profile = {
            "provider_summary": {
                "roles": {role: {} for role in ("reader", "synthesizer", "planner", "implementer", "verifier")},
                "semantic_verification": {},
            },
            "evaluation": {"max_fix_attempts": 2},
        }
        with self.assertRaises(eval_harness.EvalError) as raised:
            eval_harness.validate_budgets(args, [{"id": "one"}], [profile])
        self.assertIn("model-call", str(raised.exception))

        args.max_model_calls = 100
        args.apply = False
        with self.assertRaises(eval_harness.EvalError) as raised:
            eval_harness.validate_budgets(args, [{"id": "one"}], [profile])
        self.assertIn("--apply", str(raised.exception))

    def test_unknown_tokens_and_cost_remain_unknown(self):
        metrics = eval_harness.invocation_metrics(
            {"invocations": [{"role": "reader", "status": "success", "model": "local"}]}
        )
        self.assertEqual(metrics["prompt_tokens"], eval_harness.UNKNOWN)
        self.assertEqual(metrics["reported_cost"], eval_harness.UNKNOWN)

    def test_secret_values_are_not_retained_in_safe_profile_summary(self):
        config = {
            "roles": {
                "implementer": {
                    "transport": "openai-compatible-chat-completions",
                    "model": "provider/model",
                    "base_url": "https://user:password@example.test/v1?token=secret",
                    "api_key_env": "OPENROUTER_API_KEY",
                    "headers": {"Authorization": "Bearer TOP-SECRET"},
                }
            },
            "secret": "TOP-SECRET",
        }
        rendered = json.dumps(eval_harness.safe_provider_summary(config))
        self.assertNotIn("TOP-SECRET", rendered)
        self.assertNotIn("password", rendered)
        self.assertNotIn("token=secret", rendered)
        self.assertIn("OPENROUTER_API_KEY", rendered)

    def test_aggregation_and_markdown_keep_dimensions_visible(self):
        results = [
            {
                "case_id": "case",
                "profile": "a",
                "status": "completed",
                "comparable": True,
                "comparability_notes": [],
                "outcome": {
                    "deterministic_verification_pass": True,
                    "semantic": {"verdict": "pass"},
                },
                "minimality": {"files_changed": 1, "lines_added": 2, "lines_deleted": 1},
                "reliability": {"provider_failures": []},
                "efficiency": {"model_calls": 4, "reported_cost": eval_harness.UNKNOWN},
                "reproducibility": {
                    "provider_summary": {
                        "roles": {
                            "reader": {"transport": "command", "model": "model-a"},
                            "implementer": {"transport": "command", "model": "model-b"},
                        }
                    }
                },
            }
        ]
        cases = {"case": {"tags": ["python"]}}
        aggregate = eval_harness.aggregate(results, cases)
        report = eval_harness.render_markdown(results, aggregate)

        self.assertEqual(aggregate["profiles"]["a"]["deterministic_passes"], 1)
        self.assertEqual(aggregate["tags"]["python"]["completed"], 1)
        self.assertEqual(aggregate["provider_transports"]["command"]["runs"], 1)
        self.assertEqual(aggregate["models"]["model-a"]["runs"], 1)
        self.assertEqual(aggregate["models"]["model-b"]["runs"], 1)
        self.assertIn("Deterministic", report)
        self.assertIn("Semantic", report)
        self.assertIn("Files", report)
        self.assertIn("Calls", report)
        self.assertIn("Aggregate by provider transport", report)
        self.assertIn("Aggregate by model", report)
        self.assertIn("No opaque overall score", report)

    def test_parser_accepts_repeated_profile_selection(self):
        parser = eval_harness.build_parser()
        args = parser.parse_args(
            ["--profile", "legacy-command", "--profile", "mixed-groq-openrouter-semantic"]
        )
        self.assertEqual(args.profiles, ["legacy-command", "mixed-groq-openrouter-semantic"])


if __name__ == "__main__":
    unittest.main()
