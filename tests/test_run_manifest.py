import json
import tempfile
import unittest
from pathlib import Path

from automation.run_manifest import (
    ManifestError,
    build_role_snapshot,
    complete_stage,
    create_manifest,
    hash_file,
    invalidate_role,
    load_manifest,
    next_stage,
    reconcile_role_snapshots,
    record_failure,
    render_status,
    sanitized_invocation,
    sync_invocations,
    validate_artifacts,
)


class RunManifestTests(unittest.TestCase):
    def _snapshot(self, role: str, model: str, **extra):
        config = {
            "transport": "openai-compatible-chat-completions",
            "model": model,
            "base_url": "https://provider.example/v1",
            "api_key_env": f"{role.upper()}_API_KEY",
            **extra,
        }
        return build_role_snapshot(
            config,
            {
                "transport": config["transport"],
                "model": model,
                "base_url": config["base_url"],
                "api_key_env": config["api_key_env"],
            },
            prompt_policy={"prompt_policy_mode": "full"},
        )

    def _create(self, root: Path, *, mode="pr", snapshots=None):
        path = root / "run-manifest.json"
        create_manifest(
            path,
            repo_path=root / "repo",
            github_repo="owner/repo",
            issue_number=37,
            mode=mode,
            base_sha="base-sha",
            branch="autodev/issue-37-resume",
            role_snapshots=snapshots or {
                "reader": self._snapshot("reader", "reader-a"),
                "synthesizer": self._snapshot("synthesizer", "synth-a"),
                "planner": self._snapshot("planner", "planner-a"),
                "implementer": self._snapshot("implementer", "impl-a"),
                "fixer": self._snapshot("fixer", "fix-a"),
                "verifier": self._snapshot("verifier", "verify-a"),
            },
        )
        return path

    def _artifact(self, root: Path, name: str, value: str | None = None):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value if value is not None else name, encoding="utf-8")
        return path

    def test_manifest_records_version_target_roles_and_stage_hashes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "repo").mkdir()
            manifest_path = self._create(root)
            issue = self._artifact(root, "issue.md", "Issue #37")
            expected_hash = hash_file(issue)

            complete_stage(
                manifest_path,
                "issue-selected",
                run_root=root,
                artifacts=[issue],
                inputs={"issue": 37, "base_sha": "base-sha"},
                details={"branch": "autodev/issue-37-resume"},
            )
            manifest = load_manifest(manifest_path)

        self.assertEqual(manifest["schema_version"], 1)
        self.assertEqual(manifest["target"]["issue_number"], 37)
        self.assertIn("planner", manifest["roles"])
        self.assertEqual(
            manifest["stages"]["issue-selected"]["artifacts"]["issue.md"],
            expected_hash,
        )
        self.assertTrue(manifest["stages"]["issue-selected"]["input_hash"])
        self.assertTrue(manifest["stages"]["issue-selected"]["output_hash"])

    def test_artifact_drift_is_detected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "repo").mkdir()
            manifest_path = self._create(root)
            plan = self._artifact(root, "coder-plan.md", "original plan")
            complete_stage(manifest_path, "plan-created", run_root=root, artifacts=[plan])

            self.assertEqual(validate_artifacts(load_manifest(manifest_path), root), [])
            plan.write_text("externally changed plan", encoding="utf-8")
            problems = validate_artifacts(load_manifest(manifest_path), root)

        self.assertEqual(len(problems), 1)
        self.assertIn("artifact drift", problems[0])
        self.assertIn("coder-plan.md", problems[0])

    def test_role_fingerprint_never_depends_on_secret_value(self):
        first = build_role_snapshot(
            {"model": "m", "api_key": "secret-one", "headers": {"Authorization": "secret-two"}},
            {"model": "m", "api_key_env": "MODEL_API_KEY"},
        )
        second = build_role_snapshot(
            {"model": "m", "api_key": "different", "headers": {"Authorization": "also-different"}},
            {"model": "m", "api_key_env": "MODEL_API_KEY"},
        )

        self.assertEqual(first["fingerprint"], second["fingerprint"])
        serialized = json.dumps(first)
        self.assertNotIn("secret-one", serialized)
        self.assertNotIn("secret-two", serialized)

    def test_planner_change_invalidates_plan_and_downstream_not_reader_or_synthesis(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "repo").mkdir()
            manifest_path = self._create(root)
            artifact = self._artifact(root, "stage.txt")
            for stage in (
                "repository-read",
                "handoff-synthesized",
                "plan-created",
                "implementation-generated",
                "patch-applied",
                "deterministic-verified",
                "semantic-verified",
            ):
                complete_stage(manifest_path, stage, run_root=root, artifacts=[artifact])

            invalidated = invalidate_role(manifest_path, "planner", reason="planner changed")
            manifest = load_manifest(manifest_path)

        self.assertNotIn("repository-read", invalidated)
        self.assertNotIn("handoff-synthesized", invalidated)
        self.assertIn("plan-created", invalidated)
        self.assertIn("implementation-generated", invalidated)
        self.assertIn("repository-read", manifest["completed_stages"])
        self.assertIn("handoff-synthesized", manifest["completed_stages"])
        self.assertNotIn("plan-created", manifest["completed_stages"])

    def test_completed_role_change_requires_explicit_invalidation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "repo").mkdir()
            old = {"planner": self._snapshot("planner", "planner-a")}
            manifest_path = self._create(root, snapshots=old)
            artifact = self._artifact(root, "plan.md")
            complete_stage(manifest_path, "plan-created", run_root=root, artifacts=[artifact])
            new = {"planner": self._snapshot("planner", "planner-b")}

            with self.assertRaises(ManifestError):
                reconcile_role_snapshots(manifest_path, new)

            reconcile_role_snapshots(manifest_path, new, explicit_invalidations={"planner"})
            manifest = load_manifest(manifest_path)

        self.assertNotIn("plan-created", manifest["completed_stages"])
        self.assertEqual(manifest["roles"]["planner"]["fingerprint"], new["planner"]["fingerprint"])

    def test_unused_future_role_can_change_without_invalidation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "repo").mkdir()
            snapshots = {
                "reader": self._snapshot("reader", "reader-a"),
                "planner": self._snapshot("planner", "planner-a"),
                "implementer": self._snapshot("implementer", "impl-a"),
            }
            manifest_path = self._create(root, snapshots=snapshots)
            artifact = self._artifact(root, "reader.md")
            complete_stage(manifest_path, "repository-read", run_root=root, artifacts=[artifact])
            changed = dict(snapshots)
            changed["implementer"] = self._snapshot("implementer", "impl-b")

            reconcile_role_snapshots(manifest_path, changed)
            manifest = load_manifest(manifest_path)

        self.assertIn("repository-read", manifest["completed_stages"])
        self.assertEqual(manifest["roles"]["implementer"]["fingerprint"], changed["implementer"]["fingerprint"])

    def test_stage_sequence_reports_exact_resume_boundary(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "repo").mkdir()
            manifest_path = self._create(root, mode="pr")
            artifact = self._artifact(root, "artifact.txt")
            expected = [
                ("issue-selected", "repository-read"),
                ("repository-read", "handoff-synthesized"),
                ("handoff-synthesized", "plan-created"),
                ("plan-created", "implementation-generated"),
                ("implementation-generated", "patch-applied"),
                ("patch-applied", "deterministic-verified"),
                ("deterministic-verified", "semantic-verified"),
                ("semantic-verified", "pr-created"),
                ("pr-created", "complete"),
            ]
            observed = []
            for completed, next_expected in expected:
                complete_stage(manifest_path, completed, run_root=root, artifacts=[artifact])
                observed.append((completed, next_stage(load_manifest(manifest_path))))

        self.assertEqual(observed, expected)

    def test_plan_only_manifest_is_complete_after_plan(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "repo").mkdir()
            manifest_path = self._create(root, mode="plan-only")
            artifact = self._artifact(root, "artifact.txt")
            for stage in ("issue-selected", "repository-read", "handoff-synthesized", "plan-created"):
                complete_stage(manifest_path, stage, run_root=root, artifacts=[artifact])

            manifest = load_manifest(manifest_path)

        self.assertEqual(next_stage(manifest), "complete")

    def test_provider_failure_and_usage_are_sanitized_into_manifest(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "repo").mkdir()
            manifest_path = self._create(root)
            invocations = root / "model-invocations.json"
            invocations.write_text(
                json.dumps(
                    [
                        {
                            "role": "implementer",
                            "transport": "openai-compatible-chat-completions",
                            "model": "vendor/free:free",
                            "status": "failure",
                            "failure_classification": "rate_limited",
                            "status_code": 429,
                            "usage": {"input_tokens": 50},
                            "reported_cost": 0,
                            "base_url": "https://user:password@provider.example/v1?token=secret",
                            "authorization": "Bearer secret",
                            "response": "model text must not be copied",
                        }
                    ]
                ),
                encoding="utf-8",
            )

            sync_invocations(manifest_path, invocations)
            manifest = load_manifest(manifest_path)
            record_failure(
                manifest_path,
                classification="rate_limited",
                reason="token=secret-value provider quota exhausted",
                stage="implementation-generated",
            )
            manifest = load_manifest(manifest_path)

        call = manifest["invocations"][0]
        self.assertEqual(call["failure_classification"], "rate_limited")
        self.assertEqual(call["usage"]["input_tokens"], 50)
        self.assertEqual(call["reported_cost"], 0)
        self.assertNotIn("password", json.dumps(call))
        self.assertNotIn("model text", json.dumps(call))
        self.assertNotIn("secret-value", json.dumps(manifest["failure"]))

    def test_status_lists_completed_next_failure_and_invalidation_preview(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "repo").mkdir()
            manifest_path = self._create(root)
            artifact = self._artifact(root, "artifact.txt")
            for stage in ("issue-selected", "repository-read", "handoff-synthesized", "plan-created"):
                complete_stage(manifest_path, stage, run_root=root, artifacts=[artifact])
            record_failure(
                manifest_path,
                classification="rate_limited",
                reason="provider quota exhausted",
                stage="implementation-generated",
            )

            text = render_status(load_manifest(manifest_path), requested_invalidations=["planner"])

        self.assertIn("Completed stages:", text)
        self.assertIn("plan-created", text)
        self.assertIn("Next stage: implementation-generated", text)
        self.assertIn("Last run failure: rate_limited", text)
        self.assertIn("Requested invalidation:", text)
        self.assertIn("planner:", text)

    def test_sanitized_invocation_keeps_compression_without_prompt_text(self):
        value = sanitized_invocation(
            {
                "role": "planner",
                "status": "success",
                "compression": {
                    "status": "compressed",
                    "tokens_before": 100,
                    "tokens_after": 50,
                    "prompt": "secret repository prompt",
                },
            }
        )

        self.assertEqual(value["compression"]["tokens_before"], 100)
        self.assertNotIn("prompt", value["compression"])


if __name__ == "__main__":
    unittest.main()
