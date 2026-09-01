from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from automation import (
    privacy,
    privacy_authorization,
    privacy_grant_cli,
    privacy_grant_commands,
    privacy_grant_contract,
    privacy_grant_store,
    scheduler_registration,
)
from automation.scheduler_types import SchedulerError


class _FakeRuntime:
    name = "fake"

    def __init__(self, evidence: dict[str, privacy.PrivacyDecision]) -> None:
        self._evidence = evidence

    def privacy_evidence(self, repo: Path, *, runner, which=None):
        return dict(self._evidence)


class PrivacyAuthorizationTests(unittest.TestCase):
    @staticmethod
    def _repo(root: str) -> Path:
        repo = Path(root)
        (repo / ".git").mkdir(parents=True)
        config = repo / ".autodev" / "privacy.json"
        config.parent.mkdir(parents=True)
        config.write_text(
            json.dumps({"profile": "strict-confidential", "consent_mode": "explicit"}),
            encoding="utf-8",
        )
        return repo

    @staticmethod
    def _decision(
        role: str = "planner",
        route: str = "openai/gpt-5.6-terra",
    ) -> privacy.PrivacyDecision:
        return privacy.PrivacyDecision(
            outcome="CONSENT_REQUIRED",
            role=role,
            route=route,
            provider="openai-opencode",
            model=route.partition("/")[2],
            route_scope="direct-cloud",
            training="unknown",
            retention="unknown",
            policy_source="unknown or stale policy",
            enforcement_state="enforced-by-provider-contract",
            reason="training policy is unknown; customer-content retention is unknown",
        )

    @staticmethod
    def _environment(root: str):
        return mock.patch.dict(
            os.environ,
            {
                privacy_grant_contract.STORE_ENV: str(Path(root) / "privacy-grants.json"),
                privacy_grant_contract.REPOSITORY_ID_ENV: "github:yaron-e92/phoodab",
            },
            clear=False,
        )

    def test_runtime_neutral_authorizer_consumes_valid_persistent_grant(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self._repo(temp_dir)
            policy = privacy.load_policy(repo)
            with self._environment(temp_dir):
                record = privacy_grant_commands.create_grant(
                    repo, policy, [self._decision()], duration="7d"
                )
                allowed = privacy_authorization.authorize_headless(
                    repo, [self._decision()]
                )

            self.assertEqual(len(allowed), 1)
            self.assertEqual(allowed[0].outcome, "ALLOW")
            self.assertEqual(allowed[0].enforcement_state, "user-consented")
            audit = (repo / ".autodev-run" / privacy.PRIVACY_AUDIT).read_text(
                encoding="utf-8"
            )
            self.assertIn("persistent-consent-use", audit)
            self.assertIn(str(record["id"]), audit)

    def test_runtime_neutral_authorizer_reports_uncovered_routes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self._repo(temp_dir)
            with self._environment(temp_dir), self.assertRaises(
                privacy_authorization.PrivacyConsentRequired
            ) as raised:
                privacy_authorization.authorize_headless(
                    repo,
                    [
                        self._decision("planner", "openai/gpt-5.6-terra"),
                        self._decision("implementer", "openai/gpt-5.6-sol"),
                    ],
                )
            self.assertEqual(
                [item.role for item in raised.exception.decisions],
                ["planner", "implementer"],
            )

    def test_expired_and_revoked_grants_do_not_authorize_headless_routes(self) -> None:
        for mode in ("expired", "revoked"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temp_dir:
                repo = self._repo(temp_dir)
                policy = privacy.load_policy(repo)
                with self._environment(temp_dir):
                    if mode == "expired":
                        privacy_grant_commands.create_grant(
                            repo,
                            policy,
                            [self._decision()],
                            duration="24h",
                            now=datetime(2026, 1, 1, tzinfo=timezone.utc),
                        )
                    else:
                        record = privacy_grant_commands.create_grant(
                            repo, policy, [self._decision()], duration="7d"
                        )
                        privacy_grant_commands.revoke_grants(
                            repo, grant_id=str(record["id"])
                        )
                    with self.assertRaises(
                        privacy_authorization.PrivacyConsentRequired
                    ):
                        privacy_authorization.authorize_headless(
                            repo, [self._decision()]
                        )

    def test_github_ssh_and_https_remotes_share_privacy_repository_identity(self) -> None:
        self.assertEqual(
            privacy_grant_store._normalize_github_remote(
                "git@github.com:yaron-E92/PHOODAB.git"
            ),
            privacy_grant_store._normalize_github_remote(
                "https://github.com/yaron-E92/PHOODAB.git"
            ),
        )

    def test_privacy_consent_requirement_resolution_uses_selected_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self._repo(temp_dir)
            decision = self._decision()
            runtime = _FakeRuntime({"planner": decision})
            with mock.patch(
                "automation.privacy_grant_cli.role_runtime.select_runtime",
                return_value=(runtime, "test"),
            ):
                required = privacy_grant_cli._resolve_requirements(repo)
            self.assertEqual(required, [decision])

    def test_scheduler_preflight_accepts_valid_grant(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self._repo(temp_dir)
            policy = privacy.load_policy(repo)
            decision = self._decision()
            runtime = _FakeRuntime({"planner": decision})
            with self._environment(temp_dir):
                privacy_grant_commands.create_grant(
                    repo, policy, [decision], duration="7d"
                )
                with mock.patch(
                    "automation.scheduler_registration.role_runtime.select_runtime",
                    return_value=(runtime, "test"),
                ):
                    scheduler_registration._validate_headless_model_policy(
                        repo, runner=lambda *args, **kwargs: None, which=lambda command: command
                    )

    def test_scheduler_install_with_valid_grant_reaches_backend_registration(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self._repo(temp_dir)
            home = Path(temp_dir) / "home"
            policy = privacy.load_policy(repo)
            decision = self._decision()
            runtime = _FakeRuntime({"planner": decision})

            with self._environment(temp_dir):
                privacy_grant_commands.create_grant(
                    repo, policy, [decision], duration="7d"
                )
                with (
                    mock.patch(
                        "automation.scheduler_registration._repo_root",
                        return_value=repo,
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
                        return_value=(repo, "main"),
                    ),
                    mock.patch(
                        "automation.scheduler_registration._validate_headless_worker_transport"
                    ),
                    mock.patch(
                        "automation.scheduler_registration.role_runtime.select_runtime",
                        return_value=(runtime, "test"),
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
                        repo,
                        home=home,
                    )

            self.assertEqual(registration.github_repository, "yaron-E92/PHOODAB")
            self.assertEqual(registration.backend, "cron")
            install_backend.assert_called_once()

    def test_scheduler_install_without_grant_stops_before_backend_registration(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self._repo(temp_dir)
            home = Path(temp_dir) / "home"
            runtime = _FakeRuntime(
                {
                    "planner": self._decision(
                        "planner", "openai/gpt-5.6-terra"
                    )
                }
            )

            with self._environment(temp_dir):
                with (
                    mock.patch(
                        "automation.scheduler_registration._repo_root",
                        return_value=repo,
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
                        return_value=(repo, "main"),
                    ),
                    mock.patch(
                        "automation.scheduler_registration._validate_headless_worker_transport"
                    ),
                    mock.patch(
                        "automation.scheduler_registration.role_runtime.select_runtime",
                        return_value=(runtime, "test"),
                    ),
                    mock.patch(
                        "automation.scheduler_registration.claim_identity.worker_identity",
                        return_value="worker-test",
                    ),
                    mock.patch(
                        "automation.scheduler_registration._install_backend"
                    ) as install_backend,
                    self.assertRaises(SchedulerError) as raised,
                ):
                    scheduler_registration.install_scheduler(
                        repo,
                        home=home,
                    )

            install_backend.assert_not_called()
            message = str(raised.exception)
            self.assertIn("scheduler privacy preflight requires consent for", message)
            self.assertIn("planner", message)
            self.assertIn("openai/gpt-5.6-terra", message)
            self.assertIn("autodev privacy consent", message)

    def test_scheduler_preflight_missing_grant_is_actionable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self._repo(temp_dir)
            runtime = _FakeRuntime(
                {
                    "planner": self._decision("planner", "openai/gpt-5.6-terra"),
                    "implementer": self._decision("implementer", "openai/gpt-5.6-sol"),
                }
            )
            with self._environment(temp_dir), mock.patch(
                "automation.scheduler_registration.role_runtime.select_runtime",
                return_value=(runtime, "test"),
            ), self.assertRaises(SchedulerError) as raised:
                scheduler_registration._validate_headless_model_policy(
                    repo, runner=lambda *args, **kwargs: None, which=lambda command: command
                )

            message = str(raised.exception)
            self.assertIn("scheduler privacy preflight requires consent for", message)
            self.assertIn("planner", message)
            self.assertIn("openai/gpt-5.6-terra", message)
            self.assertIn("implementer", message)
            self.assertIn("autodev privacy consent", message)
            self.assertIn("autodev scheduler install", message)


if __name__ == "__main__":
    unittest.main()
