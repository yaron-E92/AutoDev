from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from automation import (
    opencode_adapter,
    opencode_role_runtime,
    role_coordinator_flow,
    role_coordinator_runtime,
    role_runtime,
    run_manifest,
    workflow_stages,
)


class MockRuntime:
    name = "mock"

    def __init__(self) -> None:
        self.invocations: list[role_runtime.RoleInvocationContext] = []

    def role_snapshots(self, repo: Path, *, runner, which=None) -> dict[str, object]:
        return {
            role: role_runtime.build_role_snapshot(
                runtime=self.name,
                role=role,
                configured={"implementation": "test-double-v1"},
                safe_metadata={"model": "mock/model"},
            )
            for role in opencode_adapter.ROLE_NAMES
        }

    def invoke(self, context: role_runtime.RoleInvocationContext, *, runner, which=None):
        self.invocations.append(context)
        return role_runtime.RoleInvocationResult(
            runtime=self.name,
            role=context.role,
            phase=context.phase,
            returncode=0,
            elapsed_ms=1,
            stdout="mock success",
            model="mock/model",
        )


class RoleRuntimeSelectionTests(unittest.TestCase):
    def test_no_runtime_configured_defaults_to_opencode(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(os.environ, {}, clear=True):
            repo = Path(temp_dir)
            runtime, source = role_runtime.select_runtime(repo)
            self.assertIsInstance(runtime, opencode_role_runtime.OpenCodeRoleRuntime)
            self.assertEqual(source, "default")

    def test_repository_runtime_config_is_used(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(os.environ, {}, clear=True):
            repo = Path(temp_dir)
            config = repo / ".autodev" / "config.json"
            config.parent.mkdir(parents=True)
            config.write_text(json.dumps({"role_runtime": "mock"}), encoding="utf-8")
            instance = MockRuntime()
            runtime, source = role_runtime.select_runtime(
                repo,
                registry={"mock": lambda: instance},
            )
            self.assertIs(runtime, instance)
            self.assertEqual(source, ".autodev/config.json")

    def test_environment_overrides_repository_and_explicit_overrides_environment(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            config = repo / ".autodev" / "config.json"
            config.parent.mkdir(parents=True)
            config.write_text(json.dumps({"role_runtime": "repo-runtime"}), encoding="utf-8")
            with patch.dict(os.environ, {role_runtime.RUNTIME_ENV: "env-runtime"}, clear=True):
                name, source = role_runtime.resolve_runtime_name(repo)
                self.assertEqual(
                    (name, source),
                    ("env-runtime", f"environment:{role_runtime.RUNTIME_ENV}"),
                )
                name, source = role_runtime.resolve_runtime_name(repo, "cli-runtime")
                self.assertEqual((name, source), ("cli-runtime", "explicit"))

    def test_unknown_explicit_runtime_fails_without_fallback(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaises(role_runtime.RoleRuntimeError) as raised:
                role_runtime.select_runtime(
                    Path(temp_dir),
                    requested="missing",
                    registry={"opencode": opencode_role_runtime.OpenCodeRoleRuntime},
                )
            self.assertIn("unknown AutoDev role runtime 'missing'", str(raised.exception))
            self.assertIn("opencode", str(raised.exception))

    def test_runtime_identity_changes_role_fingerprint(self):
        open_snapshot = role_runtime.build_role_snapshot(
            runtime="opencode",
            role="reader",
            configured={"model": "vendor/model"},
        )
        mock_snapshot = role_runtime.build_role_snapshot(
            runtime="mock",
            role="reader",
            configured={"model": "vendor/model"},
        )
        self.assertNotEqual(open_snapshot["fingerprint"], mock_snapshot["fingerprint"])

    def test_unvalidated_runtime_switch_does_not_overwrite_manifest_identity(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = repo / workflow_stages.CURRENT_DIR
            current.mkdir(parents=True)
            path = current / run_manifest.MANIFEST_NAME
            run_manifest.create_manifest(
                path,
                repo_path=repo,
                github_repo="owner/repo",
                issue_number=1,
                mode="issue-to-pr",
                base_sha="base",
                branch="branch",
                role_snapshots={},
            )
            role_runtime.persist_selection(
                repo,
                name="opencode",
                source="default",
                force_manifest=True,
            )
            role_runtime.persist_selection(repo, name="mock", source="explicit")
            self.assertEqual(
                role_runtime.selected_runtime_from_manifest(repo),
                "opencode",
            )
            role_runtime.persist_selection(
                repo,
                name="mock",
                source="explicit",
                force_manifest=True,
            )
            self.assertEqual(role_runtime.selected_runtime_from_manifest(repo), "mock")

    def test_completed_role_requires_invalidation_when_runtime_fingerprint_changes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            current = root / "run"
            current.mkdir()
            issue = current / "issue.md"
            reader = current / "reader-brief.md"
            issue.write_text("issue\n", encoding="utf-8")
            reader.write_text("reader\n", encoding="utf-8")
            open_snapshot = role_runtime.build_role_snapshot(
                runtime="opencode",
                role="reader",
                configured={"model": "vendor/model"},
            )
            mock_snapshot = role_runtime.build_role_snapshot(
                runtime="mock",
                role="reader",
                configured={"model": "vendor/model"},
            )
            path = current / run_manifest.MANIFEST_NAME
            run_manifest.create_manifest(
                path,
                repo_path=root,
                github_repo="owner/repo",
                issue_number=1,
                mode="issue-to-pr",
                base_sha="base",
                branch="branch",
                role_snapshots={"reader": open_snapshot},
            )
            run_manifest.complete_stage(
                path,
                "issue-selected",
                run_root=current,
                artifacts=[issue],
            )
            run_manifest.complete_stage(
                path,
                "repository-read",
                run_root=current,
                artifacts=[reader],
            )
            with self.assertRaises(run_manifest.ManifestError):
                run_manifest.reconcile_role_snapshots(
                    path,
                    {"reader": mock_snapshot},
                )

            run_manifest.reconcile_role_snapshots(
                path,
                {"reader": mock_snapshot},
                explicit_invalidations={"reader"},
            )
            manifest = run_manifest.load_manifest(path)
            self.assertNotIn("repository-read", manifest["completed_stages"])
            self.assertEqual(
                manifest["roles"]["reader"]["fingerprint"],
                mock_snapshot["fingerprint"],
            )


class OpenCodeRoleRuntimeTests(unittest.TestCase):
    def test_opencode_runtime_preserves_agent_command_and_model_mapping(self):
        runtime = opencode_role_runtime.OpenCodeRoleRuntime()
        calls = []
        completed = SimpleNamespace(
            returncode=0,
            stdout='{"type":"text"}\n',
            stderr="",
        )

        def runner(command, **kwargs):
            calls.append((command, kwargs))
            return completed

        context = role_runtime.RoleInvocationContext(
            repo=Path(".").resolve(),
            role="reader",
            prompt="role prompt",
            timeout_seconds=17,
        )
        with patch.object(
            opencode_role_runtime.opencode_cli,
            "resolve_opencode_cli",
            return_value="/usr/bin/opencode",
        ), patch.object(
            opencode_adapter,
            "resolve_opencode_model_mappings",
            return_value={
                "reader": {
                    "agent": "autodev-reader",
                    "model": "vendor/model",
                    "source": "explicit",
                }
            },
        ), patch.object(
            opencode_role_runtime.privacy,
            "load_policy",
            return_value=SimpleNamespace(enabled=False),
        ):
            result = runtime.invoke(context, runner=runner)

        command, kwargs = calls[0]
        self.assertEqual(
            command[:4],
            ["/usr/bin/opencode", "run", "--agent", "autodev-reader"],
        )
        self.assertIn("--dir", command)
        self.assertIn("--format", command)
        self.assertEqual(command[-1], "role prompt")
        self.assertEqual(kwargs["timeout"], 17)
        self.assertEqual(result.runtime, "opencode")
        self.assertEqual(result.model, "vendor/model")


class RuntimeAgnosticCoordinatorTests(unittest.TestCase):
    def test_mock_runtime_executes_reader_synthesizer_planner_through_same_coordinator(self):
        runtime = MockRuntime()
        cursors = [
            {"state": "RESUME", "next_action": "reader", "issue_number": 29},
            {"state": "RESUME", "next_action": "synthesizer", "issue_number": 29},
            {"state": "RESUME", "next_action": "planner", "issue_number": 29},
            {"state": "COMPLETE", "next_action": "complete", "issue_number": 29},
        ]

        def accepted(role: str) -> dict[str, object]:
            return {
                "state": "ACCEPTED",
                "role": role,
                "artifact": f".autodev-run/current/{role}.out",
                "sha256": "abc",
            }

        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            role_coordinator_flow.opencode_runtime,
            "install_workflow_guards",
        ), patch.object(
            role_coordinator_flow,
            "run_stage",
            side_effect=[{"state": "CONTINUE"}, {"state": "CONTINUE"}],
        ) as stage, patch.object(
            role_coordinator_flow,
            "_resume_payload",
            side_effect=cursors,
        ), patch.object(
            role_coordinator_runtime,
            "_prepare_role",
        ), patch.object(
            role_coordinator_runtime,
            "_accept_role",
            return_value=[],
        ), patch.object(
            role_coordinator_runtime,
            "role_acceptance",
            side_effect=lambda repo, role: accepted(role),
        ):
            result = role_coordinator_flow.coordinate(
                Path(temp_dir),
                arguments="29",
                runtime_name="mock",
                runtime_registry={"mock": lambda: runtime},
            )

        self.assertEqual(result["state"], "PR_READY")
        self.assertEqual(result["role_runtime"], "mock")
        self.assertEqual(
            [context.role for context in runtime.invocations],
            ["reader", "synthesizer", "planner"],
        )
        self.assertEqual(stage.call_args_list[0].args[1], "preflight")
        self.assertEqual(stage.call_args_list[1].args[1], "prepare")

    def test_runtime_success_does_not_override_missing_durable_acceptance(self):
        runtime = MockRuntime()
        snapshots = runtime.role_snapshots(
            Path("."),
            runner=lambda *args, **kwargs: None,
        )
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            role_coordinator_runtime,
            "_prepare_role",
        ), patch.object(
            role_coordinator_runtime,
            "_accept_role",
            return_value=[],
        ), patch.object(
            role_coordinator_runtime,
            "role_acceptance",
            return_value={"state": "MISSING", "reason": "not accepted"},
        ):
            with self.assertRaises(role_coordinator_runtime.RoleCoordinatorError) as raised:
                role_coordinator_runtime.run_role(
                    Path(temp_dir),
                    "reader",
                    runtime,
                    snapshots,
                )
        self.assertIn("not durably accepted", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
