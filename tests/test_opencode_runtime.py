import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from automation import opencode_adapter, opencode_runtime, workflow_stages


class OpenCodeRuntimeTests(unittest.TestCase):
    def test_supported_root_opencode_config_is_ignored_only_for_opencode_runtime(self):
        opencode_runtime.install_workflow_guards()

        self.assertTrue(workflow_stages.ignored_workspace_path("opencode.json"))
        self.assertTrue(workflow_stages.ignored_workspace_path("opencode.jsonc"))
        self.assertFalse(workflow_stages.ignored_workspace_path("src/opencode.jsonc"))
        self.assertFalse(workflow_stages.ignored_workspace_path("opencode.jsonc.backup"))
        self.assertFalse(workflow_stages.ignored_workspace_path("src/product.cs"))

    def test_terminal_failed_preserves_originating_failure_context(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = repo / workflow_stages.CURRENT_DIR
            current.mkdir(parents=True)
            (current / "state.json").write_text(
                json.dumps(
                    {
                        "IssueNumber": 29,
                        "BranchName": "autodev/issue-29",
                        "Status": "Prepared",
                        "RepoFullName": "",
                    }
                ),
                encoding="utf-8",
            )
            (current / opencode_runtime.FRONTEND_FAILURE_FILE).write_text(
                json.dumps(
                    {
                        "issue_number": 29,
                        "failed_stage": "prepare",
                        "reason": "prepared worktree is not clean: opencode.jsonc",
                        "failure_classification": "non-retryable-deterministic",
                        "failure_fingerprint": "abc123",
                    }
                ),
                encoding="utf-8",
            )
            args = opencode_adapter.build_parser().parse_args(
                [
                    "stage",
                    "--name",
                    "failed",
                    "--repo",
                    str(repo),
                    "--reason",
                    "OpenCode coordinator failed",
                ]
            )

            output = io.StringIO()
            with (
                patch("automation.opencode_runtime.workflow_stages.mark_blocked"),
                patch("automation.opencode_runtime.opencode_resume.has_manifest", return_value=False),
                redirect_stdout(output),
            ):
                code = opencode_runtime._terminal_failed(args)

            payload = json.loads(output.getvalue())
            self.assertEqual(code, 0)
            self.assertEqual(payload["issue_number"], 29)
            self.assertEqual(payload["stage"], "failed")
            self.assertEqual(payload["failed_stage"], "prepare")
            self.assertEqual(payload["reason"], "prepared worktree is not clean: opencode.jsonc")
            self.assertEqual(payload["failure_classification"], "non-retryable-deterministic")
            self.assertEqual(payload["failure_fingerprint"], "abc123")

    def test_failed_stage_payload_is_persisted_for_terminal_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            payload = {
                "state": "FAILED",
                "issue_number": 29,
                "failed_stage": "preflight",
                "reason": "required setup missing",
                "failure_classification": "non-retryable-deterministic",
                "failure_fingerprint": "fingerprint",
            }
            out = json.dumps(payload) + "\n"

            with patch(
                "automation.opencode_runtime._run_adapter",
                return_value=(1, out, ""),
            ):
                code = opencode_runtime.run(
                    [
                        "stage",
                        "--name",
                        "preflight",
                        "--repo",
                        str(repo),
                        "--arguments",
                        "29",
                    ]
                )

            persisted = json.loads(
                (repo / workflow_stages.CURRENT_DIR / opencode_runtime.FRONTEND_FAILURE_FILE).read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(code, 1)
            self.assertEqual(persisted["issue_number"], 29)
            self.assertEqual(persisted["failed_stage"], "preflight")
            self.assertEqual(persisted["reason"], "required setup missing")
            self.assertEqual(persisted["failure_fingerprint"], "fingerprint")

    def test_portable_wrapper_invokes_hardened_runtime(self):
        wrapper = (
            Path(__file__).resolve().parents[1] / "integrations" / "opencode" / "autodev.py"
        ).read_text(encoding="utf-8")
        self.assertIn('"automation.opencode_runtime"', wrapper)
        self.assertNotIn('"automation.opencode_adapter",\n            *_arguments_with_current_issue', wrapper)


if __name__ == "__main__":
    unittest.main()
