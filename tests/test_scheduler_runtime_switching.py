from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from automation import (
    opencode_adapter_contract,
    opencode_scheduler_runtime,
    role_runtime,
    scheduler_backends,
    scheduler_health_cli,
    scheduler_registration,
)
from automation.scheduler_types import SchedulerRegistration


class FakeRuntime:
    name = "fake"

    def __init__(self, identity: str = "v1") -> None:
        self.identity = identity

    def provision_scheduler_worker(self, repo: Path, **_kwargs) -> None:
        path = repo / ".fake-runtime" / "ready.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"identity": self.identity}) + "\n", encoding="utf-8")

    def validate_scheduler_worker(self, repo: Path, **_kwargs) -> None:
        if not (repo / ".fake-runtime" / "ready.json").is_file():
            raise role_runtime.RoleRuntimeError("fake runtime is not provisioned")

    def validate_scheduler_routes(self, repo: Path, **_kwargs) -> None:
        self.validate_scheduler_worker(repo)

    def scheduler_runtime_environment(self, _repo: Path, **_kwargs) -> dict[str, str]:
        return {"PATH": "/fake/bin"}

    def scheduler_runtime_identity(self, _repo: Path, **_kwargs) -> dict[str, object]:
        return {"runtime": self.name, "contract": self.identity}

    def scheduler_runtime_routes(self, _repo: Path, **_kwargs) -> dict[str, str]:
        return {"reader": "fake/local"}


class SchedulerRuntimeSwitchingTests(unittest.TestCase):
    def _repo(self, root: Path, name: str) -> Path:
        repo = root / name
        repo.mkdir(parents=True)
        (repo / ".git").mkdir()
        return repo

    def test_generic_runtime_provisions_without_opencode_assets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            worker = self._repo(Path(temp_dir), "worker")
            state = role_runtime.prepare_scheduler_worker(
                FakeRuntime(),
                worker,
                source="test",
                runner=lambda *_args, **_kwargs: None,
            )

            self.assertTrue((worker / ".fake-runtime" / "ready.json").is_file())
            self.assertFalse((worker / ".opencode").exists())
            self.assertEqual(state.name, "fake")
            self.assertEqual(state.routes, {"reader": "fake/local"})
            self.assertEqual(state.environment["PATH"], "/fake/bin")

    def test_runtime_identity_changes_scheduler_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            worker = self._repo(Path(temp_dir), "worker")
            first = role_runtime.prepare_scheduler_worker(
                FakeRuntime("v1"), worker, runner=lambda *_args, **_kwargs: None
            )
            second = role_runtime.prepare_scheduler_worker(
                FakeRuntime("v2"), worker, runner=lambda *_args, **_kwargs: None
            )
            self.assertNotEqual(first.fingerprint, second.fingerprint)

    def test_scheduler_resolution_ignores_ambient_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self._repo(Path(temp_dir), "repo")
            config = repo / ".autodev" / "config.json"
            config.parent.mkdir()
            config.write_text('{"role_runtime":"fake"}\n', encoding="utf-8")
            with mock.patch.dict(
                os.environ,
                {role_runtime.RUNTIME_ENV: "ambient"},
                clear=False,
            ):
                name, source = role_runtime.resolve_scheduler_runtime_name(repo)
                explicit, explicit_source = role_runtime.resolve_scheduler_runtime_name(
                    repo, "opencode"
                )

            self.assertEqual(name, "fake")
            self.assertEqual(source, ".autodev/config.json")
            self.assertEqual(explicit, "opencode")
            self.assertEqual(explicit_source, "explicit-scheduler")

    def test_worker_sidecar_pins_runtime_ahead_of_ambient_shell(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            worker = self._repo(Path(temp_dir), "worker")
            state = role_runtime.SchedulerRuntimeState(
                name="fake",
                source="test",
                identity={"runtime": "fake"},
                environment={"PATH": "/fake/bin"},
                routes={"reader": "fake/local"},
            )
            role_runtime.write_scheduler_runtime_state(worker, state)
            with mock.patch.dict(
                os.environ,
                {role_runtime.RUNTIME_ENV: "opencode"},
                clear=False,
            ):
                name, source = role_runtime.resolve_runtime_name(worker)
            self.assertEqual((name, source), ("fake", "scheduler-registration"))

    def test_windows_task_uses_registered_runtime_path_not_ambient_path(self) -> None:
        state = role_runtime.SchedulerRuntimeState(
            name="fake",
            source="test",
            identity={"runtime": "fake"},
            environment={"PATH": r"C:\fake-runtime\bin;C:\Windows\System32"},
            routes={"reader": "fake/local"},
        )
        registration = SchedulerRegistration(
            github_repository="owner/repo",
            source_repository=r"C:\source",
            worker_repository=r"C:\worker",
            default_branch="main",
            backend="windows-task",
            cadence_minutes=15,
            launcher=r"C:\AutoDev\autodev.exe",
            task_id="autodev-owner-repo",
            installed_at="2026-09-07T00:00:00Z",
            role_runtime="fake",
            runtime_state=state.to_json(),
        )
        with mock.patch.dict(os.environ, {"PATH": r"C:\wrong"}, clear=False):
            action = scheduler_backends._windows_task_action(
                registration, Path(r"C:\state\registration.json")
            )
        self.assertIn(r"C:\fake-runtime\bin", action)
        self.assertNotIn(r"C:\wrong", action)

    def test_failed_runtime_switch_restores_registration_and_worker_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = self._repo(root, "source")
            worker = self._repo(root, "worker")
            path = root / "registration.json"
            old_state = role_runtime.SchedulerRuntimeState(
                name="opencode",
                source="old",
                identity={"runtime": "opencode", "version": "old"},
                environment={"PATH": "/old/bin"},
                routes={"reader": "ollama/old"},
            )
            new_state = role_runtime.SchedulerRuntimeState(
                name="fake",
                source="explicit-scheduler",
                identity={"runtime": "fake", "version": "new"},
                environment={"PATH": "/fake/bin"},
                routes={"reader": "fake/local"},
            )
            old = SchedulerRegistration(
                github_repository="owner/repo",
                source_repository=str(source),
                worker_repository=str(worker),
                default_branch="main",
                backend="cron",
                cadence_minutes=15,
                launcher="/usr/bin/autodev",
                task_id="autodev-owner-repo",
                installed_at="2026-09-07T00:00:00Z",
                role_runtime="opencode",
                runtime_state=old_state.to_json(),
            )
            scheduler_registration._write_registration(path, old)
            role_runtime.write_scheduler_runtime_state(worker, old_state)

            with (
                mock.patch.object(scheduler_registration, "_validate_source_policy"),
                mock.patch.object(scheduler_registration, "_validate_worker_policy"),
                mock.patch.object(scheduler_registration, "_validate_headless_worker_transport"),
                mock.patch.object(
                    scheduler_registration,
                    "_prepare_runtime_registration",
                    return_value=(FakeRuntime(), new_state),
                ),
                mock.patch.object(
                    scheduler_registration,
                    "_install_backend",
                    side_effect=RuntimeError("backend failed"),
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "backend failed"):
                    scheduler_registration.switch_scheduler_runtime(
                        path,
                        "fake",
                        runner=lambda *_args, **_kwargs: SimpleNamespace(returncode=0),
                    )

            restored = scheduler_registration._load_registration(path)
            self.assertIsNotNone(restored)
            assert restored is not None
            self.assertEqual(restored.role_runtime, "opencode")
            self.assertEqual(
                role_runtime.scheduler_runtime_state(worker).fingerprint,  # type: ignore[union-attr]
                old_state.fingerprint,
            )

    def test_install_runtime_option_is_removed_before_legacy_scheduler_parser(self) -> None:
        stripped, runtime_name = scheduler_health_cli._extract_install_runtime(
            ["install", "--repo", ".", "--runtime", "fake", "--cadence-minutes", "30"]
        )
        self.assertEqual(runtime_name, "fake")
        self.assertEqual(
            stripped,
            ["install", "--repo", ".", "--cadence-minutes", "30"],
        )


class OpenCodeSchedulerRuntimeTests(unittest.TestCase):
    def _runtime_with_routes(self, route: str) -> opencode_scheduler_runtime.OpenCodeSchedulerRoleRuntime:
        runtime = opencode_scheduler_runtime.OpenCodeSchedulerRoleRuntime()
        runtime._mappings = {
            role: {
                "agent": opencode_adapter_contract.AUTODEV_AGENT_BY_ROLE[role],
                "source": "autodev-profile:user:test",
                "model": route,
                "inherits_from": "",
            }
            for role in opencode_adapter_contract.ROLE_NAMES
        }
        return runtime

    def test_local_ollama_route_materializes_non_secret_provider_registration(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            runtime = self._runtime_with_routes("ollama/gpt-oss:20b-autodev")
            with mock.patch.dict(os.environ, {"PATH": "/usr/bin"}, clear=False):
                environment = runtime.scheduler_runtime_environment(
                    repo,
                    runner=lambda *_args, **_kwargs: SimpleNamespace(returncode=0),
                    which=lambda command: f"/usr/bin/{command}",
                )
            overlay = json.loads(environment["OPENCODE_CONFIG_CONTENT"])
            ollama = overlay["provider"]["ollama"]
            self.assertEqual(ollama["options"]["baseURL"], "http://localhost:11434/v1")
            self.assertIn("gpt-oss:20b-autodev", ollama["models"])
            rendered = json.dumps(overlay).casefold()
            self.assertNotIn("api_key", rendered)
            self.assertNotIn("token", rendered)
            self.assertNotIn("password", rendered)

    def test_model_catalog_validation_passes_without_model_invocation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            runtime = self._runtime_with_routes("ollama/gpt-oss:20b-autodev")
            calls: list[list[str]] = []

            def runner(command, **_kwargs):
                argv = [str(item) for item in command]
                calls.append(argv)
                if argv[-1:] == ["models"]:
                    return SimpleNamespace(
                        returncode=0,
                        stdout="ollama/gpt-oss:20b-autodev\n",
                        stderr="",
                    )
                raise AssertionError(argv)

            runtime.validate_scheduler_routes(
                repo,
                runner=runner,
                which=lambda _command: "opencode",
            )
            self.assertIn(["opencode", "models"], calls)
            self.assertFalse(any("run" in call[1:] for call in calls))

    def test_model_catalog_validation_reports_unregistered_route(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            runtime = self._runtime_with_routes("ollama/gpt-oss:20b-autodev")

            def runner(command, **_kwargs):
                argv = [str(item) for item in command]
                if argv[-1:] == ["models"]:
                    return SimpleNamespace(
                        returncode=0,
                        stdout="openai/gpt-5.6-sol\n",
                        stderr="",
                    )
                raise AssertionError(argv)

            with self.assertRaisesRegex(
                role_runtime.RoleRuntimeError,
                "not registered/discoverable",
            ):
                runtime.validate_scheduler_routes(
                    repo,
                    runner=runner,
                    which=lambda _command: "opencode",
                )


if __name__ == "__main__":
    unittest.main()
