import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path


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

    def _write_state(self, repo: Path, issue_number: int) -> None:
        current = repo / ".codex-run" / "current"
        current.mkdir(parents=True)
        (current / "state.json").write_text(
            json.dumps({"IssueNumber": issue_number}),
            encoding="utf-8",
        )


if __name__ == "__main__":
    unittest.main()
