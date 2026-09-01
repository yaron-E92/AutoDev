from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from automation import (
    opencode_adapter_contract,
    opencode_role_runtime,
    role_runtime,
    scheduler_registration,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


class OpenCodeRunner:
    def __init__(self, *, missing: set[str] | None = None) -> None:
        self.missing = set(missing or set())
        self.calls: list[list[str]] = []

    def __call__(self, command, **kwargs):
        argv = [str(item) for item in command]
        self.calls.append(argv)
        if argv and argv[0] == "git":
            return subprocess.run(command, **kwargs)
        if argv[:3] == ["opencode", "agent", "list"]:
            agents = [
                opencode_adapter_contract.AUTODEV_AGENT_BY_ROLE[role]
                for role in opencode_adapter_contract.ROLE_NAMES
                if opencode_adapter_contract.AUTODEV_AGENT_BY_ROLE[role]
                not in self.missing
            ]
            output = "\n".join(f"{name} (all)\n  []" for name in agents)
            return SimpleNamespace(returncode=0, stdout=output, stderr="")
        if argv[:3] == ["opencode", "debug", "config"]:
            agents = {
                opencode_adapter_contract.AUTODEV_AGENT_BY_ROLE[role]: {}
                for role in opencode_adapter_contract.ROLE_NAMES
                if opencode_adapter_contract.AUTODEV_AGENT_BY_ROLE[role]
                not in self.missing
            }
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps({"agent": agents}),
                stderr="",
            )
        raise AssertionError(f"unexpected command: {argv}")


class SchedulerRuntimePreflightTests(unittest.TestCase):
    def _git_repo(self, path: Path) -> Path:
        path.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(path)], check=True)
        return path

    def test_opencode_runtime_provisions_and_discovers_all_workflow_agents_model_free(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            worker = self._git_repo(Path(temp_dir) / "worker")
            runner = OpenCodeRunner()
            runtime = opencode_role_runtime.OpenCodeRoleRuntime()

            runtime.provision_scheduler_worker(
                worker, runner=runner, which=lambda command: command
            )
            runtime.validate_scheduler_worker(
                worker, runner=runner, which=lambda command: command
            )

            for role in opencode_adapter_contract.ROLE_NAMES:
                agent = opencode_adapter_contract.AUTODEV_AGENT_BY_ROLE[role]
                self.assertTrue(
                    (worker / ".opencode" / "agents" / f"{agent}.md").is_file()
                )
            self.assertIn(["opencode", "agent", "list"], runner.calls)
            self.assertIn(["opencode", "debug", "config"], runner.calls)
            self.assertFalse(
                any(call[:2] == ["opencode", "run"] for call in runner.calls)
            )

    def test_opencode_runtime_preflight_reports_missing_discoverable_agent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            worker = self._git_repo(Path(temp_dir) / "worker")
            missing = {"autodev-reader"}
            runner = OpenCodeRunner(missing=missing)
            runtime = opencode_role_runtime.OpenCodeRoleRuntime()
            runtime.provision_scheduler_worker(
                worker, runner=runner, which=lambda command: command
            )

            with self.assertRaisesRegex(
                role_runtime.RoleRuntimeError,
                "autodev-reader",
            ) as raised:
                runtime.validate_scheduler_worker(
                    worker, runner=runner, which=lambda command: command
                )

            self.assertIn("not discoverable", str(raised.exception))
            self.assertIn("autodev scheduler install", str(raised.exception))

    def test_scheduler_install_provisions_worker_before_backend_registration(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = self._git_repo(root / "source")
            worker = self._git_repo(root / "worker")
            home = root / "home"
            local_reader = source / ".opencode" / "agents" / "autodev-reader.md"
            local_reader.parent.mkdir(parents=True)
            local_reader.write_text("source-local fixture\n", encoding="utf-8")
            self.assertFalse(
                (worker / ".opencode" / "agents" / "autodev-reader.md").exists()
            )

            runner = OpenCodeRunner()
            runtime = opencode_role_runtime.OpenCodeRoleRuntime()
            with (
                mock.patch(
                    "automation.scheduler_registration._repo_root",
                    return_value=source,
                ),
                mock.patch(
                    "automation.scheduler_registration._validate_source_policy"
                ),
                mock.patch(
                    "automation.scheduler_registration.queue_github.resolve_github_repo",
                    return_value="yaron-E92/PHOODAB",
                ),
                mock.patch(
                    "automation.scheduler_registration._select_backend",
                    return_value="cron",
                ),
                mock.patch(
                    "automation.scheduler_registration._resolve_launcher",
                    return_value="/usr/bin/autodev",
                ),
                mock.patch(
                    "automation.scheduler_registration._ensure_worker",
                    return_value=(worker, "main"),
                ),
                mock.patch(
                    "automation.scheduler_registration._validate_headless_worker_transport"
                ),
                mock.patch(
                    "automation.scheduler_registration.role_runtime.select_runtime",
                    return_value=(runtime, "test"),
                ),
                mock.patch(
                    "automation.scheduler_registration._validate_headless_model_policy"
                ),
                mock.patch(
                    "automation.scheduler_registration.claim_identity.worker_identity",
                    return_value="worker-test",
                ),
                mock.patch(
                    "automation.scheduler_registration._install_backend"
                ) as install_backend,
            ):
                registration = scheduler_registration.install_scheduler(
                    source,
                    home=home,
                    runner=runner,
                    which=lambda command: command,
                )

            self.assertEqual(registration.github_repository, "yaron-E92/PHOODAB")
            self.assertTrue(
                (worker / ".opencode" / "agents" / "autodev-reader.md").is_file()
            )
            self.assertEqual(
                local_reader.read_text(encoding="utf-8"),
                "source-local fixture\n",
            )
            install_backend.assert_called_once()

    def test_runtime_prepare_hook_is_optional_for_future_non_asset_runtime(self) -> None:
        class MinimalRuntime:
            name = "minimal"

        role_runtime.prepare_scheduler_worker(
            MinimalRuntime(),
            Path("."),
            runner=lambda *args, **kwargs: None,
        )


if __name__ == "__main__":
    unittest.main()
