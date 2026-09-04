from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from contextlib import redirect_stderr
from unittest import mock

from automation import (
    autodev_cli,
    privacy,
    privacy_authorization,
    privacy_grant_commands,
    privacy_grant_contract,
    privacy_grant_store,
    scheduler_registration,
)


class _FakeRuntime:
    name = "fake"

    def __init__(self, decision: privacy.PrivacyDecision) -> None:
        self._decision = decision

    def privacy_evidence(self, repo: Path, *, runner, which=None):
        return {self._decision.role: self._decision}


class RepositoryTargetOverrideTests(unittest.TestCase):
    @staticmethod
    def _repo(root: Path, github_repository: str) -> Path:
        repo = root
        (repo / ".git").mkdir(parents=True)
        autodev = repo / ".autodev"
        autodev.mkdir(parents=True, exist_ok=True)
        (autodev / "repo.json").write_text(
            json.dumps({"version": 1, "github_repository": github_repository}),
            encoding="utf-8",
        )
        (autodev / "privacy.json").write_text(
            json.dumps({"profile": "strict-confidential", "consent_mode": "explicit"}),
            encoding="utf-8",
        )
        return repo

    @staticmethod
    def _decision() -> privacy.PrivacyDecision:
        return privacy.PrivacyDecision(
            outcome="CONSENT_REQUIRED",
            role="planner",
            route="openai/gpt-5.6-terra",
            provider="openai-opencode",
            model="gpt-5.6-terra",
            route_scope="direct-cloud",
            training="unknown",
            retention="unknown",
            policy_source="unknown or stale policy",
            enforcement_state="enforced-by-provider-contract",
            reason="training policy is unknown; customer-content retention is unknown",
        )

    def test_global_owner_repo_apply_to_any_command_and_restore_environment(self) -> None:
        captured: dict[str, str] = {}

        def privacy_cli(_args):
            captured["owner"] = os.environ.get("GITHUB_OWNER", "")
            captured["repo"] = os.environ.get("GITHUB_REPO", "")
            return 0

        with mock.patch.dict(
            os.environ,
            {"GITHUB_OWNER": "old-owner", "GITHUB_REPO": "old-repo"},
            clear=False,
        ), mock.patch.object(
            autodev_cli.privacy_grant_cli, "run_cli", side_effect=privacy_cli
        ):
            code = autodev_cli.run(
                [
                    "--owner",
                    "com-mit-group",
                    "--repo",
                    "ShuffleTask",
                    "privacy",
                    "status",
                ]
            )
            restored = (
                os.environ.get("GITHUB_OWNER"),
                os.environ.get("GITHUB_REPO"),
            )

        self.assertEqual(code, 0)
        self.assertEqual(captured, {"owner": "com-mit-group", "repo": "ShuffleTask"})
        self.assertEqual(restored, ("old-owner", "old-repo"))

    def test_global_repository_target_requires_owner_and_repo(self) -> None:
        error = io.StringIO()
        with redirect_stderr(error):
            code = autodev_cli.run(["--owner", "com-mit-group", "privacy", "status"])
        self.assertEqual(code, 2)
        self.assertIn("requires both --owner and --repo", error.getvalue())
        self.assertIn("missing --repo", error.getvalue())

    def test_issue_to_pr_working_directory_repo_option_remains_compatible(self) -> None:
        with mock.patch.object(
            autodev_cli, "_enable_interactive_consent_for_direct_cli"
        ), mock.patch.object(
            autodev_cli.opencode_entrypoint, "run", return_value=0
        ) as run_entrypoint:
            code = autodev_cli.run(
                [
                    "--owner",
                    "com-mit-group",
                    "--repo",
                    "ShuffleTask",
                    "issue-to-pr",
                    "338",
                    "--repo",
                    "../ShuffleTask",
                ]
            )
        self.assertEqual(code, 0)
        run_entrypoint.assert_called_once_with(
            [
                "coordinate",
                "--arguments",
                "338",
                "--repo",
                "../ShuffleTask",
            ]
        )

    def test_privacy_identity_prefers_repository_config_over_stale_origin(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self._repo(Path(temp_dir), "com-mit-group/ShuffleTask")

            def stale_origin(argv, **_kwargs):
                self.assertEqual(argv[:4], ["git", "remote", "get-url", "--all"])
                return SimpleNamespace(
                    returncode=0,
                    stdout="git@github.com:yaron-E92/ShuffleTask.git\n",
                    stderr="",
                )

            identity = privacy_grant_store.repository_identity(repo, runner=stale_origin)

        self.assertEqual(identity, "github:com-mit-group/shuffletask")

    def test_environment_target_wins_over_repository_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self._repo(Path(temp_dir), "configured-owner/ShuffleTask")
            with mock.patch.dict(
                os.environ,
                {"GITHUB_OWNER": "com-mit-group", "GITHUB_REPO": "ShuffleTask"},
                clear=False,
            ):
                identity = privacy_grant_store.repository_identity(repo)
        self.assertEqual(identity, "github:com-mit-group/shuffletask")

    def test_grant_created_in_stale_source_is_consumed_by_canonical_worker(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = self._repo(root / "source", "com-mit-group/ShuffleTask")
            worker = self._repo(root / "worker", "com-mit-group/ShuffleTask")
            store = root / "privacy-grants.json"
            decision = self._decision()
            runtime = _FakeRuntime(decision)
            with mock.patch.dict(
                os.environ,
                {privacy_grant_contract.STORE_ENV: str(store)},
                clear=False,
            ):
                policy = privacy.load_policy(source)
                privacy_grant_commands.create_grant(
                    source, policy, [decision], duration="7d"
                )
                scheduler_registration._validate_headless_model_policy(
                    worker,
                    runner=lambda *args, **kwargs: None,
                    which=lambda command: command,
                    runtime=runtime,
                )
                allowed = privacy_authorization.authorize_headless(
                    worker, [self._decision()]
                )

        self.assertEqual(len(allowed), 1)
        self.assertEqual(allowed[0].outcome, "ALLOW")
        self.assertEqual(allowed[0].enforcement_state, "user-consented")


if __name__ == "__main__":
    unittest.main()
