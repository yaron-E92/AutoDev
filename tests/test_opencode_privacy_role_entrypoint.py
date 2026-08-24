import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from automation import (
    opencode_adapter,
    opencode_install,
    opencode_role_entrypoint,
    opencode_role_runtime,
    role_coordinator_runtime,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


class OpenCodePrivacyRoleEntrypointTests(unittest.TestCase):
    def test_installer_replaces_standalone_role_commands_with_python_gated_runner(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir)
            opencode_install.install_assets(target, REPO_ROOT, python_command="python3")

            for command, role in (
                ("autodev-read.md", "reader"),
                ("autodev-plan.md", "planner"),
                ("autodev-implement.md", "implementer"),
                ("autodev-fix.md", "fixer"),
                ("autodev-verify.md", "verifier"),
            ):
                text = (target / ".opencode" / "commands" / command).read_text(
                    encoding="utf-8"
                )
                self.assertIn("agent: build", text)
                self.assertIn(f"autodev role --role {role}", text)
                self.assertNotIn(".opencode/autodev.py role", text)
                self.assertNotIn(opencode_install.PYTHON_SHELL_PLACEHOLDER, text)
                self.assertIn("display-only", text)

    def test_role_entrypoint_prepares_then_uses_runtime_role_runner(self):
        runtime = Mock()
        runtime.name = "opencode"
        runtime.role_snapshots.return_value = {"planner": {"fingerprint": "fp"}}
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            opencode_role_entrypoint.opencode_runtime,
            "install_workflow_guards",
        ), patch.object(
            opencode_role_runtime,
            "OpenCodeRoleRuntime",
            return_value=runtime,
        ), patch.object(
            opencode_adapter,
            "prepare_role",
        ) as prepare, patch.object(
            role_coordinator_runtime,
            "run_role",
            return_value={
                "state": "ACCEPTED",
                "role": "planner",
                "artifact": ".autodev-run/current/plan.md",
            },
        ) as run_role, patch("builtins.print") as output:
            code = opencode_role_entrypoint.run(
                ["--role", "planner", "--repo", temp_dir, "--arguments", "112"]
            )

        repo = Path(temp_dir).resolve()
        self.assertEqual(code, 0)
        runtime.validate_arguments.assert_called_once_with("112")
        runtime.role_snapshots.assert_called_once_with(
            repo,
            runner=opencode_role_entrypoint.subprocess.run,
        )
        prepare.assert_called_once_with("planner", repo, "112")
        run_role.assert_called_once_with(
            repo,
            "planner",
            runtime,
            {"planner": {"fingerprint": "fp"}},
            already_prepared=True,
            runner=opencode_role_entrypoint.subprocess.run,
        )
        payload = json.loads(output.call_args.args[0])
        self.assertEqual(payload["state"], "ACCEPTED")
        self.assertEqual(payload["runtime"], "opencode")
        self.assertEqual(payload["artifact"], ".autodev-run/current/plan.md")

    def test_role_entrypoint_returns_failed_without_leaking_prompt_content(self):
        runtime = Mock()
        runtime.name = "opencode"
        runtime.role_snapshots.return_value = {"planner": {"fingerprint": "fp"}}
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            opencode_role_entrypoint.opencode_runtime,
            "install_workflow_guards",
        ), patch.object(
            opencode_role_runtime,
            "OpenCodeRoleRuntime",
            return_value=runtime,
        ), patch.object(
            opencode_adapter,
            "prepare_role",
        ), patch.object(
            role_coordinator_runtime,
            "run_role",
            side_effect=role_coordinator_runtime.RoleCoordinatorError(
                "privacy blocked planner route provider/model",
                classification="privacy_blocked",
            ),
        ), patch("builtins.print") as output:
            code = opencode_role_entrypoint.run(
                ["--role", "planner", "--repo", temp_dir, "--arguments", "112"]
            )

        self.assertEqual(code, 1)
        payload = json.loads(output.call_args.args[0])
        self.assertEqual(payload["classification"], "privacy_blocked")
        self.assertEqual(payload["runtime"], "opencode")
        self.assertNotIn("prompt", json.dumps(payload).casefold())


if __name__ == "__main__":
    unittest.main()
