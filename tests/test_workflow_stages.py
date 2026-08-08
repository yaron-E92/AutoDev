import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from automation import workflow_stages


REPO_ROOT = Path(__file__).resolve().parents[1]


class WorkflowStageTests(unittest.TestCase):
    def test_preflight_requires_git_and_gh_but_not_pwsh(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            (repo / ".git").mkdir()
            checked = []

            def fake_which(name):
                checked.append(name)
                return f"/tools/{name}"

            with patch.dict(os.environ, {"GITHUB_OWNER": "owner", "GITHUB_REPO": "repo"}, clear=False):
                code, payload = workflow_stages.execute_stage(
                    "preflight",
                    repo,
                    arguments="65",
                    which=fake_which,
                )

            self.assertEqual(code, 0)
            self.assertEqual(payload["state"], "CONTINUE")
            self.assertEqual(checked, ["git", "gh"])
            self.assertNotIn("pwsh", checked)

    def test_preflight_fails_before_mutation_when_tool_is_missing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            (repo / ".git").mkdir()

            with patch.dict(os.environ, {"GITHUB_OWNER": "owner", "GITHUB_REPO": "repo"}, clear=False):
                with self.assertRaises(workflow_stages.WorkflowStageError):
                    workflow_stages.execute_stage(
                        "preflight",
                        repo,
                        arguments="65",
                        which=lambda name: None if name == "gh" else f"/tools/{name}",
                    )

            self.assertFalse((repo / ".codex-run").exists())

    def test_prepare_writes_cross_platform_state_without_model_calls(self):
        issue = {
            "number": 65,
            "title": "Portable stages",
            "body": "Implement portable stages.",
            "url": "https://example.test/issues/65",
            "labels": [{"name": "area:python"}],
        }
        base_ref = {"object": {"sha": "base-sha"}}
        base_commit = {"tree": {"sha": "tree-sha"}}

        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            (repo / ".git").mkdir()

            with (
                patch.dict(
                    os.environ,
                    {
                        "GITHUB_OWNER": "owner",
                        "GITHUB_REPO": "repo",
                        "LOCAL_CHECK": "python -m unittest",
                        "STACK_CONTEXT": "Python",
                    },
                    clear=False,
                ),
                patch("automation.workflow_stages.gh_json", side_effect=[issue, base_ref, base_commit]),
                patch("automation.workflow_stages.gh") as gh,
            ):
                current = workflow_stages.ensure_prepared_issue(repo, "65", autodev_root=REPO_ROOT)

            state = json.loads((current / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["IssueNumber"], 65)
            self.assertEqual(state["BaseSha"], "base-sha")
            self.assertEqual(state["BaseTreeSha"], "tree-sha")
            self.assertEqual(state["LocalCheck"], "python -m unittest")
            self.assertEqual(state["Status"], "Prepared")
            self.assertTrue((current / "workspace-snapshot.json").is_file())
            self.assertTrue(str(current.resolve()) in state["RunDir"])
            gh.assert_called_once()

    def test_prepare_rejects_missing_remote_base_tree_before_issue_mutation(self):
        issue = {
            "number": 67,
            "title": "Hardening",
            "body": "Harden it.",
            "url": "https://example.test/issues/67",
            "labels": [],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            (repo / ".git").mkdir()
            with (
                patch.dict(
                    os.environ,
                    {
                        "GITHUB_OWNER": "owner",
                        "GITHUB_REPO": "repo",
                        "LOCAL_CHECK": "check",
                    },
                    clear=False,
                ),
                patch(
                    "automation.workflow_stages.gh_json",
                    side_effect=[issue, {"object": {"sha": "base-sha"}}, {"tree": {}, "sha": "base-sha"}],
                ),
                patch("automation.workflow_stages.gh") as gh,
            ):
                with self.assertRaises(workflow_stages.WorkflowStageError) as raised:
                    workflow_stages.ensure_prepared_issue(repo, "67", autodev_root=REPO_ROOT)

            self.assertIn("tree.sha", str(raised.exception))
            self.assertIn("base-sha", str(raised.exception))
            gh.assert_not_called()
            self.assertFalse((repo / ".codex-run" / "current" / "state.json").exists())

    def test_prepare_reuses_matching_current_issue_without_github_mutation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = self._write_state(repo, IssueNumber=65)

            with patch("automation.workflow_stages.gh", side_effect=AssertionError("unexpected mutation")):
                actual = workflow_stages.ensure_prepared_issue(repo, "65", autodev_root=REPO_ROOT)

            self.assertTrue(actual.samefile(current))

    def test_render_implementer_uses_existing_template_and_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = self._write_state(
                repo,
                IssueText="# Issue",
                LocalCheck="check",
                StackContext="context",
            )
            (current / "issue.md").write_text("# Issue\n", encoding="utf-8")
            (current / "plan.md").write_text("Plan body\n", encoding="utf-8")
            state = workflow_stages.read_state(current)

            workflow_stages.render_implementer_prompt(repo, current, state, REPO_ROOT)

            rendered = (current / "implementer.md").read_text(encoding="utf-8")
            state = workflow_stages.read_state(current)
            self.assertIn("Plan body", rendered)
            self.assertEqual(state["Status"], "ImplementerPromptRendered")

    def test_local_check_failure_renders_repair_and_preserves_exit_contract(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = self._write_state(
                repo,
                IssueText="# Issue",
                LocalCheck="failing-check",
                StackContext="context",
            )
            (current / "issue.md").write_text("# Issue\n", encoding="utf-8")
            (current / "workspace-snapshot.json").write_text("{}\n", encoding="utf-8")

            def runner(*args, **kwargs):
                return SimpleNamespace(returncode=1, stdout="failure output\n", stderr="")

            with patch.dict(os.environ, {"MAX_REPAIR_ATTEMPTS": "1"}, clear=False):
                _, repair = workflow_stages.execute_stage(
                    "local-check",
                    repo,
                    autodev_root=REPO_ROOT,
                    attempt=0,
                    runner=runner,
                )
                _, blocked = workflow_stages.execute_stage(
                    "local-check",
                    repo,
                    autodev_root=REPO_ROOT,
                    attempt=1,
                    runner=runner,
                )

            self.assertEqual(repair["state"], "REPAIR")
            self.assertEqual(repair["failure_classification"], workflow_stages.FAILURE_CODE_REPAIRABLE)
            self.assertEqual(blocked["state"], "BLOCKED")
            self.assertTrue((current / "local-repair.md").is_file())
            self.assertEqual(workflow_stages.read_state(current)["Status"], "LocalCheckFailed")

    def test_local_check_capture_uses_safe_utf8_replacement_on_invalid_bytes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = self._write_state(repo, LocalCheck="failing-check")
            (current / "workspace-snapshot.json").write_text("{}\n", encoding="utf-8")
            calls = []

            def runner(command, **kwargs):
                calls.append((command, kwargs))
                return SimpleNamespace(returncode=7, stdout=b"stdout:\x81\n", stderr=b"stderr:\x9d\n")

            _, payload = workflow_stages.execute_stage(
                "local-check",
                repo,
                autodev_root=REPO_ROOT,
                runner=runner,
            )

            log = (current / "local-check.log").read_text(encoding="utf-8")
            self.assertEqual(payload["state"], "REPAIR")
            self.assertIn("\ufffd", log)
            self.assertEqual(calls[0][1]["encoding"], "utf-8")
            self.assertEqual(calls[0][1]["errors"], "replace")
            self.assertTrue(calls[0][1]["text"])

    def test_gh_failure_preserves_exit_code_and_invalid_bytes_without_decode_crash(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)

            def runner(*args, **kwargs):
                return SimpleNamespace(returncode=9, stdout=b"", stderr=b"bad:\x81")

            with self.assertRaises(workflow_stages.WorkflowStageError) as raised:
                workflow_stages.gh(repo, ["api", "repos/owner/repo"], runner=runner)

            self.assertIn("exited with 9", str(raised.exception))
            self.assertIn("\ufffd", str(raised.exception))

    def test_gh_json_rejects_replacement_corrupted_machine_json(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)

            def runner(*args, **kwargs):
                return SimpleNamespace(returncode=0, stdout=b'{"value":"\x81"}', stderr=b"")

            with self.assertRaises(workflow_stages.WorkflowStageError) as raised:
                workflow_stages.gh_json(repo, ["api", "repos/owner/repo"], runner=runner)

            self.assertIn("invalid JSON", str(raised.exception))

    def test_local_check_passes_with_native_platform_shell(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = self._write_state(repo, LocalCheck="successful-check")
            (current / "workspace-snapshot.json").write_text("{}\n", encoding="utf-8")
            calls = []

            def runner(command, **kwargs):
                calls.append((command, kwargs))
                return SimpleNamespace(returncode=0, stdout="ok\n", stderr="")

            _, payload = workflow_stages.execute_stage(
                "local-check",
                repo,
                autodev_root=REPO_ROOT,
                runner=runner,
            )

            self.assertEqual(payload["state"], "CONTINUE")
            self.assertEqual(calls[0][0], "successful-check")
            self.assertTrue(calls[0][1]["shell"])
            self.assertEqual(calls[0][1]["encoding"], "utf-8")
            self.assertEqual(calls[0][1]["errors"], "replace")
            self.assertEqual(workflow_stages.read_state(current)["Status"], "LocalCheckPassed")
            diagnostics = json.loads((current / "run-diagnostics.json").read_text(encoding="utf-8"))
            self.assertEqual(diagnostics["stage_invocations"]["local-check"], 1)
            self.assertEqual(len(diagnostics["stage_wall_time_ms"]["local-check"]), 1)

    def test_semantic_stage_maps_pass_repair_blocked_and_exhaustion(self):
        repair = {
            "verdict": "repair",
            "requirements": [{"criterion": "criterion", "status": "missing", "evidence": ["diff"]}],
            "findings": [{"severity": "blocking", "message": "missing", "path": "file.py"}],
            "repair_brief": "Fix it.",
        }
        blocked = {
            "verdict": "blocked",
            "requirements": [{"criterion": "criterion", "status": "uncertain", "evidence": ["missing evidence"]}],
            "findings": [{"severity": "blocking", "message": "blocked", "path": "file.py"}],
            "repair_brief": "",
        }
        passed = {
            "verdict": "pass",
            "requirements": [{"criterion": "criterion", "status": "met", "evidence": ["diff"]}],
            "findings": [],
            "repair_brief": "",
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = self._write_state(repo)
            (current / "issue.md").write_text("# Issue\n", encoding="utf-8")
            (current / "plan.md").write_text("Plan\n", encoding="utf-8")
            result = current / "verification-result.json"

            def fake_prepare(*args):
                Path(args[-1]).write_text("repair prompt\n", encoding="utf-8")

            with (
                patch.dict(os.environ, {"MAX_SEMANTIC_REPAIR_ATTEMPTS": "1"}, clear=False),
                patch("automation.workflow_stages.prepare_semantic_repair_prompt", side_effect=fake_prepare),
            ):
                result.write_text(json.dumps(repair), encoding="utf-8")
                _, repair_payload = workflow_stages.execute_stage("semantic", repo, autodev_root=REPO_ROOT, attempt=0)
                _, exhausted = workflow_stages.execute_stage("semantic", repo, autodev_root=REPO_ROOT, attempt=1)
                result.write_text(json.dumps(blocked), encoding="utf-8")
                _, blocked_payload = workflow_stages.execute_stage("semantic", repo, autodev_root=REPO_ROOT)
                result.write_text(json.dumps(passed), encoding="utf-8")
                _, pass_payload = workflow_stages.execute_stage("semantic", repo, autodev_root=REPO_ROOT)

            self.assertEqual(repair_payload["state"], "REPAIR")
            self.assertEqual(exhausted["state"], "BLOCKED")
            self.assertEqual(blocked_payload["state"], "BLOCKED")
            self.assertEqual(pass_payload["state"], "CONTINUE")
            self.assertTrue((current / "verification-repair.md").is_file())

    def test_semantic_open_code_prerequisite_requires_accepted_result(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = self._write_state(
                repo,
                OpenCodeProtocolVersion=1,
                AcceptedRoleArtifacts={},
            )
            (current / "issue.md").write_text("# Issue\n", encoding="utf-8")

            with self.assertRaises(workflow_stages.WorkflowStageError) as raised:
                workflow_stages.execute_stage("semantic", repo)

            self.assertIn("verification-result.json is missing", str(raised.exception))

    def test_recorded_deterministic_failure_suppresses_unchanged_retry(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = self._write_state(
                repo,
                OpenCodeProtocolVersion=1,
                AcceptedRoleArtifacts={},
            )
            (current / "issue.md").write_text("# Issue\n", encoding="utf-8")

            try:
                workflow_stages.execute_stage("semantic", repo)
            except workflow_stages.WorkflowStageError as exc:
                first = workflow_stages.record_stage_failure(repo, "semantic", exc)
            else:
                self.fail("semantic prerequisite should fail")

            code, repeated = workflow_stages.execute_stage("semantic", repo)

            self.assertEqual(first["failure_classification"], workflow_stages.FAILURE_DETERMINISTIC)
            self.assertEqual(code, 1)
            self.assertEqual(repeated["state"], "FAILED")
            self.assertTrue(repeated["repeated_failure"])
            diagnostics = json.loads((current / "run-diagnostics.json").read_text(encoding="utf-8"))
            self.assertEqual(diagnostics["repeated_identical_failures"], 1)

    def test_create_api_commit_recovers_and_persists_missing_prepared_tree(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = self._write_state(
                repo,
                RepoFullName="owner/repo",
                BaseSha="base-sha",
                BaseTreeSha="",
                BranchName="autodev/issue-67",
            )
            state = workflow_stages.read_state(current)
            with (
                patch(
                    "automation.workflow_stages.gh_json",
                    side_effect=[
                        {"tree": {"sha": "resolved-base-tree"}},
                        {"sha": "new-tree"},
                        {"sha": "new-commit"},
                    ],
                ),
                patch(
                    "automation.workflow_stages.gh",
                    side_effect=[
                        SimpleNamespace(returncode=1, stdout="", stderr="not found"),
                        SimpleNamespace(returncode=0, stdout="", stderr=""),
                    ],
                ),
            ):
                sha = workflow_stages.create_api_commit(
                    repo,
                    state,
                    [],
                    current,
                )

            self.assertEqual(sha, "new-commit")
            self.assertEqual(
                workflow_stages.read_state(current)["BaseTreeSha"],
                "resolved-base-tree",
            )

    def test_create_api_commit_missing_parent_tree_keeps_underlying_evidence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = self._write_state(
                repo,
                RepoFullName="owner/repo",
                BaseSha="base-sha",
                BaseTreeSha="",
                BranchName="autodev/issue-67",
            )
            with patch(
                "automation.workflow_stages.gh_json",
                return_value={"sha": "base-sha", "tree": {}, "message": "malformed fixture"},
            ):
                with self.assertRaises(workflow_stages.WorkflowStageError) as raised:
                    workflow_stages.create_api_commit(
                        repo,
                        workflow_stages.read_state(current),
                        [],
                        current,
                    )

            self.assertIn("base-sha", str(raised.exception))
            self.assertIn("malformed fixture", str(raised.exception))

    def test_pr_and_ci_reuses_existing_pr_and_records_api_commit(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = self._write_state(
                repo,
                RepoFullName="owner/repo",
                Base="main",
                BaseSha="base",
                BaseTreeSha="tree",
                BranchName="autodev/issue-65",
                IssueTitle="Issue 65",
                IssueText="# Issue",
                LocalCheck="check",
                PrUrl="https://example.test/pr/1",
                PrNumber=1,
            )
            (current / "workspace-snapshot.json").write_text("{}\n", encoding="utf-8")
            (current / "issue.md").write_text("# Issue\n", encoding="utf-8")
            (current / "plan.md").write_text("Plan\n", encoding="utf-8")
            (repo / "changed.txt").write_text("changed\n", encoding="utf-8")

            with (
                patch("automation.workflow_stages.create_api_commit", return_value="commit-sha") as create_commit,
                patch("automation.workflow_stages.wait_for_required_checks", return_value=[]),
                patch("automation.workflow_stages.render_legacy_verifier") as render_verifier,
            ):
                passed = workflow_stages.pr_and_ci(
                    repo,
                    current,
                    workflow_stages.read_state(current),
                    REPO_ROOT,
                )

            self.assertTrue(passed)
            create_commit.assert_called_once()
            render_verifier.assert_called_once()
            state = workflow_stages.read_state(current)
            self.assertEqual(state["LastCommitSha"], "commit-sha")
            self.assertEqual(state["PrUrl"], "https://example.test/pr/1")
            self.assertEqual(state["Status"], "CiPassedVerifierPromptRendered")
            self.assertTrue((current / "last-commit-workspace-snapshot.json").is_file())

    def test_pr_and_ci_mocked_commit_to_new_pr_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = self._write_state(
                repo,
                RepoFullName="owner/repo",
                Base="main",
                BaseSha="base",
                BaseTreeSha="tree",
                BranchName="autodev/issue-67",
                IssueTitle="Issue 67",
                IssueText="# Issue",
                LocalCheck="check",
            )
            (current / "workspace-snapshot.json").write_text("{}\n", encoding="utf-8")
            (current / "issue.md").write_text("# Issue\n", encoding="utf-8")
            (current / "plan.md").write_text("Plan\n", encoding="utf-8")
            (repo / "changed.txt").write_text("changed\n", encoding="utf-8")

            def create_pr(repo_arg, current_arg, state_arg, **kwargs):
                state_arg["PrUrl"] = "https://example.test/pr/67"
                state_arg["PrNumber"] = 67
                workflow_stages.write_state(current_arg, state_arg)

            with (
                patch("automation.workflow_stages.create_api_commit", return_value="commit-sha"),
                patch("automation.workflow_stages.ensure_pr", side_effect=create_pr) as ensure_pr,
                patch("automation.workflow_stages.wait_for_required_checks", return_value=[]),
                patch("automation.workflow_stages.render_legacy_verifier"),
            ):
                passed = workflow_stages.pr_and_ci(
                    repo,
                    current,
                    workflow_stages.read_state(current),
                    REPO_ROOT,
                )

            self.assertTrue(passed)
            ensure_pr.assert_called_once()
            state = workflow_stages.read_state(current)
            self.assertEqual(state["LastCommitSha"], "commit-sha")
            self.assertEqual(state["PrNumber"], 67)

    def test_ensure_pr_does_not_create_duplicate_pr(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = self._write_state(repo, PrUrl="https://example.test/pr/1", PrNumber=1)

            def runner(*args, **kwargs):
                self.fail("gh must not run when the PR is already recorded")

            workflow_stages.ensure_pr(repo, current, workflow_stages.read_state(current), runner=runner)

    def test_ci_failure_renders_repair_and_coordinator_maps_exhaustion(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = self._write_state(repo)
            (current / "workspace-snapshot.json").write_text("{}\n", encoding="utf-8")

            with (
                patch("automation.workflow_stages.pr_and_ci", return_value=False),
                patch.dict(os.environ, {"MAX_REPAIR_ATTEMPTS": "1"}, clear=False),
            ):
                _, repair = workflow_stages.execute_stage("pr-and-ci", repo, autodev_root=REPO_ROOT, attempt=0)
                _, blocked = workflow_stages.execute_stage("pr-and-ci", repo, autodev_root=REPO_ROOT, attempt=1)

            self.assertEqual(repair["state"], "REPAIR")
            self.assertEqual(repair["failure_classification"], workflow_stages.FAILURE_CODE_REPAIRABLE)
            self.assertEqual(blocked["state"], "BLOCKED")

    def test_ready_and_blocked_reuse_existing_issue_state_contract(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = self._write_state(
                repo,
                RepoFullName="owner/repo",
                PrUrl="https://example.test/pr/1",
            )
            (current / "workspace-snapshot.json").write_text("{}\n", encoding="utf-8")

            with patch("automation.workflow_stages.gh") as gh:
                _, ready = workflow_stages.execute_stage("ready", repo)
                self.assertEqual(ready["state"], "PR_READY")
                self.assertEqual(workflow_stages.read_state(current)["Status"], "ReadyForReview")
                _, blocked = workflow_stages.execute_stage("blocked", repo, reason="manual review")

            self.assertEqual(blocked["state"], "BLOCKED")
            self.assertEqual(workflow_stages.read_state(current)["Status"], "Blocked")
            self.assertGreaterEqual(gh.call_count, 4)

    def test_failed_stage_is_coherent_without_prepared_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            code, payload = workflow_stages.execute_stage("failed", repo, reason="preflight failure")

            self.assertEqual(code, 0)
            self.assertEqual(payload["state"], "FAILED")
            self.assertEqual(payload["issue_number"], 0)
            self.assertIn("preflight failure", payload["reason"])

    def test_workspace_snapshot_ignores_platform_noise_and_uses_posix_artifact_paths(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            (repo / "src").mkdir()
            (repo / "src" / "file.txt").write_text("content\n", encoding="utf-8")
            (repo / "obj").mkdir()
            (repo / "obj" / "generated.txt").write_text("ignored\n", encoding="utf-8")
            (repo / ".codex-run").mkdir()
            (repo / ".codex-run" / "state.txt").write_text("ignored\n", encoding="utf-8")

            snapshot = workflow_stages.workspace_snapshot(repo)

            self.assertIn("src/file.txt", snapshot)
            self.assertNotIn("obj/generated.txt", snapshot)
            self.assertNotIn(".codex-run/state.txt", snapshot)
            self.assertTrue(all("\\" not in path for path in snapshot))

    def _write_state(self, repo: Path, **overrides):
        current = repo / ".codex-run" / "current"
        current.mkdir(parents=True, exist_ok=True)
        state = {
            "IssueNumber": 65,
            "Status": "Prepared",
            "BranchName": "autodev/issue-65",
            "LastCommitSha": "",
            "PrUrl": "",
            "PrNumber": 0,
            "ProviderProfile": "",
        }
        state.update(overrides)
        (current / "state.json").write_text(json.dumps(state), encoding="utf-8")
        return current


if __name__ == "__main__":
    unittest.main()
