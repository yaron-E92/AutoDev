from automation import opencode_resume_contract
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from automation import opencode_runtime, workflow_stages


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self, limit=-1):
        return json.dumps(self.payload).encode("utf-8")


class OpenCodeRuntimeTests(unittest.TestCase):
    def test_supported_root_opencode_config_is_ignored_only_for_opencode_runtime(self):
        original = workflow_stages.ignored_workspace_path
        try:
            opencode_runtime.install_workflow_guards()

            self.assertTrue(workflow_stages.ignored_workspace_path("opencode.json"))
            self.assertTrue(workflow_stages.ignored_workspace_path("opencode.jsonc"))
            self.assertFalse(workflow_stages.ignored_workspace_path("src/opencode.jsonc"))
            self.assertFalse(workflow_stages.ignored_workspace_path("opencode.jsonc.backup"))
            self.assertFalse(workflow_stages.ignored_workspace_path("src/product.cs"))
        finally:
            workflow_stages.ignored_workspace_path = original

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
                        "branch": "autodev/issue-29",
                        "completed_stage": "Prepared",
                        "failed_stage": "prepare",
                        "reason": "prepared worktree is not clean: opencode.jsonc",
                        "failure_classification": "non-retryable-deterministic",
                        "failure_fingerprint": "abc123",
                    }
                ),
                encoding="utf-8",
            )
            args = opencode_adapter_cli.build_parser().parse_args(
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
                patch("automation.opencode_runtime.opencode_resume_contract.has_manifest", return_value=False),
                redirect_stdout(output),
            ):
                code = opencode_runtime._terminal_failed(args)

            payload = json.loads(output.getvalue())
            self.assertEqual(code, 0)
            self.assertEqual(payload["issue_number"], 29)
            self.assertEqual(payload["branch"], "autodev/issue-29")
            self.assertEqual(payload["completed_stage"], "Prepared")
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
                "branch": "autodev/issue-29",
                "completed_stage": "Prepared",
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
            self.assertEqual(persisted["branch"], "autodev/issue-29")
            self.assertEqual(persisted["completed_stage"], "Prepared")
            self.assertEqual(persisted["failed_stage"], "preflight")
            self.assertEqual(persisted["reason"], "required setup missing")
            self.assertEqual(persisted["failure_fingerprint"], "fingerprint")

    def test_successful_stage_clears_stale_persisted_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            failure = repo / workflow_stages.CURRENT_DIR / opencode_runtime.FRONTEND_FAILURE_FILE
            failure.parent.mkdir(parents=True)
            failure.write_text("{}\n", encoding="utf-8")
            out = json.dumps({"state": "CONTINUE"}) + "\n"

            with patch(
                "automation.opencode_runtime._run_adapter",
                return_value=(0, out, ""),
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

            self.assertEqual(code, 0)
            self.assertFalse(failure.exists())

    def test_role_check_requires_durable_acceptance_and_artifact_hash(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = repo / workflow_stages.CURRENT_DIR
            current.mkdir(parents=True)
            artifact = current / "reader-brief.md"
            artifact.write_text("reader evidence\n", encoding="utf-8")
            digest = __import__("hashlib").sha256(artifact.read_bytes()).hexdigest()
            (current / "state.json").write_text(
                json.dumps(
                    {
                        "AcceptedRoleArtifacts": {
                            "reader": {
                                "artifact": ".autodev-run/current/reader-brief.md",
                                "sha256": digest,
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )

            output = io.StringIO()
            with (
                patch(
                    "automation.opencode_runtime._role_diagnostics",
                    return_value={"provider": "ollama", "model": "ollama/model"},
                ),
                redirect_stdout(output),
            ):
                code = opencode_runtime._role_check(["--role", "reader", "--repo", str(repo)])
            payload = json.loads(output.getvalue())

            self.assertEqual(code, 0)
            self.assertEqual(payload["state"], "ACCEPTED")
            self.assertEqual(payload["diagnostics"]["model"], "ollama/model")
            artifact.write_text("changed\n", encoding="utf-8")

            output = io.StringIO()
            with (
                patch("automation.opencode_runtime._role_diagnostics", return_value={}),
                redirect_stdout(output),
            ):
                code = opencode_runtime._role_check(["--role", "reader", "--repo", str(repo)])
            payload = json.loads(output.getvalue())
            self.assertEqual(code, 1)
            self.assertEqual(payload["state"], "STALE")

    def test_role_check_accepts_fileless_fixer_accept_record(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = repo / workflow_stages.CURRENT_DIR
            current.mkdir(parents=True)
            (current / "state.json").write_text(
                json.dumps(
                    {
                        "AcceptedRoleArtifacts": {
                            "fixer": {
                                "artifact": "target repository edits only",
                                "sha256": "",
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )

            output = io.StringIO()
            with (
                patch("automation.opencode_runtime._role_diagnostics", return_value={}),
                redirect_stdout(output),
            ):
                code = opencode_runtime._role_check(["--role", "fixer", "--repo", str(repo)])
            payload = json.loads(output.getvalue())

            self.assertEqual(code, 0)
            self.assertEqual(payload["state"], "ACCEPTED")
            self.assertEqual(payload["role"], "fixer")

    def test_role_check_rejects_unaccepted_task_even_if_child_claimed_success(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = repo / workflow_stages.CURRENT_DIR
            current.mkdir(parents=True)
            (current / "state.json").write_text("{}\n", encoding="utf-8")

            output = io.StringIO()
            with (
                patch(
                    "automation.opencode_runtime._role_diagnostics",
                    return_value={
                        "provider": "groq",
                        "model": "groq/openai/gpt-oss-120b",
                        "input_artifacts": [{"artifact": "synthesizer.md", "bytes": 123}],
                    },
                ),
                redirect_stdout(output),
            ):
                code = opencode_runtime._role_check(["--role", "synthesizer", "--repo", str(repo)])
            payload = json.loads(output.getvalue())

            self.assertEqual(code, 1)
            self.assertEqual(payload["state"], "MISSING")
            self.assertEqual(payload["role"], "synthesizer")
            self.assertEqual(payload["diagnostics"]["provider"], "groq")
            self.assertEqual(payload["diagnostics"]["model"], "groq/openai/gpt-oss-120b")

    def test_role_diagnostics_are_bounded_to_model_identity_and_artifact_sizes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = repo / workflow_stages.CURRENT_DIR
            current.mkdir(parents=True)
            (current / "synthesizer.md").write_text("bounded prompt", encoding="utf-8")
            mappings = {
                "synthesizer": {
                    "model": "groq/openai/gpt-oss-120b",
                    "source": "explicit",
                }
            }

            with (
                patch(
                    "automation.opencode_runtime.opencode_adapter_models.resolve_opencode_model_mappings",
                    return_value=mappings,
                ),
                patch(
                    "automation.opencode_runtime._headroom_diagnostics",
                    return_value={"expected": False},
                ),
            ):
                diagnostics = opencode_runtime._role_diagnostics(repo, "synthesizer")

            self.assertEqual(diagnostics["provider"], "groq")
            self.assertEqual(diagnostics["model"], "groq/openai/gpt-oss-120b")
            self.assertEqual(diagnostics["model_source"], "explicit")
            self.assertEqual(diagnostics["input_artifacts"][0]["bytes"], len(b"bounded prompt"))
            rendered = json.dumps(diagnostics).casefold()
            self.assertNotIn("bounded prompt", rendered)
            self.assertNotIn("api_key", rendered)
            self.assertNotIn("secret", rendered)

    def test_headroom_diagnostics_distinguish_proxy_bypass_and_unhealthy_kompress(self):
        health = {
            "status": "healthy",
            "ready": True,
            "checks": {
                "kompress": {"status": "unhealthy", "ready": False},
            },
        }
        with (
            patch.dict(
                "automation.opencode_runtime.os.environ",
                {
                    "OPENCODE_CONFIG_CONTENT": '{"provider":{"headroom":{}}}',
                    "HEADROOM_PORT": "8787",
                },
                clear=True,
            ),
            patch(
                "automation.opencode_runtime.urllib.request.urlopen",
                return_value=FakeResponse(health),
            ),
        ):
            diagnostics = opencode_runtime._headroom_diagnostics("ollama")

        self.assertTrue(diagnostics["expected"])
        self.assertEqual(diagnostics["routing"], "bypassed")
        self.assertTrue(diagnostics["proxy_reachable"])
        self.assertEqual(diagnostics["proxy_status"], "healthy")
        self.assertTrue(diagnostics["proxy_ready"])
        self.assertEqual(diagnostics["kompress_status"], "unhealthy")
        self.assertFalse(diagnostics["kompress_ready"])

    def test_headroom_diagnostics_are_optional_when_wrapper_is_not_expected(self):
        with patch.dict("automation.opencode_runtime.os.environ", {}, clear=True):
            diagnostics = opencode_runtime._headroom_diagnostics("ollama")

        self.assertFalse(diagnostics["expected"])
        self.assertEqual(diagnostics["routing"], "not-requested")
        self.assertEqual(diagnostics["proxy_status"], "not-checked")

    def test_portable_wrapper_routes_through_first_class_cli(self):
        wrapper = (
            Path(__file__).resolve().parents[1] / "integrations" / "opencode" / "autodev.py"
        ).read_text(encoding="utf-8")
        self.assertIn('shutil.which("autodev")', wrapper)
        self.assertIn('"automation.autodev_cli"', wrapper)
        self.assertNotIn('"automation.opencode_runtime"', wrapper)
        self.assertNotIn('"automation.opencode_adapter",\n            *_arguments_with_current_issue', wrapper)


if __name__ == "__main__":
    unittest.main()

from automation import opencode_adapter_cli

from automation import opencode_adapter_models
