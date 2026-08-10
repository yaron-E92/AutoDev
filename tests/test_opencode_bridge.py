import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from automation import workflow_verify_current


REPO_ROOT = Path(__file__).resolve().parents[1]
BRIDGE_PATH = REPO_ROOT / "integrations" / "opencode" / "autodev.py"
SPEC = importlib.util.spec_from_file_location("autodev_opencode_bridge", BRIDGE_PATH)
assert SPEC is not None and SPEC.loader is not None
BRIDGE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BRIDGE)


class OpenCodeBridgeTests(unittest.TestCase):
    def test_prepare_without_arguments_reuses_current_issue(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            self._write_state(repo, 65)
            old_cwd = Path.cwd()
            try:
                os.chdir(repo)
                actual = BRIDGE._arguments_with_current_issue(["prepare", "--role", "planner"])
            finally:
                os.chdir(old_cwd)

            self.assertEqual(
                actual,
                ["prepare", "--role", "planner", "--arguments", "65"],
            )

    def test_repair_kind_keeps_kind_and_adds_current_issue(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            self._write_state(repo, 65)
            old_cwd = Path.cwd()
            try:
                os.chdir(repo)
                actual = BRIDGE._arguments_with_current_issue(
                    ["prepare", "--role", "fixer", "--arguments", "semantic"]
                )
            finally:
                os.chdir(old_cwd)

            self.assertEqual(actual[-1], "65 semantic")

    def test_explicit_issue_is_not_rewritten(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            self._write_state(repo, 65)
            old_cwd = Path.cwd()
            try:
                os.chdir(repo)
                original = ["prepare", "--role", "fixer", "--arguments", "66 ci"]
                actual = BRIDGE._arguments_with_current_issue(original)
            finally:
                os.chdir(old_cwd)

            self.assertEqual(actual, original)

    def test_linux_bridge_defaults_to_current_profile_verifier_without_pwsh(self):
        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(BRIDGE.os, "name", "posix"),
        ):
            env = BRIDGE._bridge_environment("python3", REPO_ROOT, REPO_ROOT)

        self.assertIn("automation.workflow_verify_current", env["LOCAL_CHECK"])
        self.assertNotIn("pwsh", env["LOCAL_CHECK"])
        self.assertIn(str(REPO_ROOT), env["PYTHONPATH"])

    def test_explicit_linux_local_check_is_preserved(self):
        with (
            patch.dict(os.environ, {"LOCAL_CHECK": "custom-check"}, clear=True),
            patch.object(BRIDGE.os, "name", "posix"),
        ):
            env = BRIDGE._bridge_environment("python3", REPO_ROOT, REPO_ROOT)

        self.assertEqual(env["LOCAL_CHECK"], "custom-check")

    def test_linux_current_profile_verifier_uses_resolved_profiles(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = repo / ".autodev-run" / "current"
            current.mkdir(parents=True)
            (current / "state.json").write_text(
                json.dumps({"ProfilesCsv": "backend,maui"}),
                encoding="utf-8",
            )

            with patch("automation.workflow_verify_current.subprocess.run") as runner:
                runner.return_value.returncode = 0
                code = workflow_verify_current.run(repo, REPO_ROOT)

            self.assertEqual(code, 0)
            command = runner.call_args.args[0]
            self.assertEqual(command[0], "bash")
            self.assertEqual(Path(command[1]).parts[-3:], ("linux", "scripts", "codex-verify.sh"))
            self.assertEqual(command[-2:], ["--profiles", "backend,maui"])

    def _write_state(self, repo: Path, issue_number: int) -> None:
        current = repo / ".autodev-run" / "current"
        current.mkdir(parents=True)
        (current / "state.json").write_text(
            json.dumps({"IssueNumber": issue_number}),
            encoding="utf-8",
        )


if __name__ == "__main__":
    unittest.main()
