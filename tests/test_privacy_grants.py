from __future__ import annotations

from automation import privacy_grant_cli, privacy_grant_commands, privacy_grant_contract, privacy_grant_hooks, privacy_grant_matching

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from automation import privacy, privacy_consent, run_manifest, workflow_stages


class PrivacyGrantTests(unittest.TestCase):
    def _repo(self, root: str, *, profile: str = "strict-confidential", consent_mode: str = "explicit") -> Path:
        repo = Path(root)
        (repo / ".git").mkdir(parents=True)
        config = repo / ".autodev" / "privacy.json"
        config.parent.mkdir(parents=True)
        config.write_text(
            json.dumps({"profile": profile, "consent_mode": consent_mode}),
            encoding="utf-8",
        )
        current = repo / workflow_stages.CURRENT_DIR
        current.mkdir(parents=True)
        run_manifest.create_manifest(
            current / run_manifest.MANIFEST_NAME,
            repo_path=repo,
            github_repo="owner/repo",
            issue_number=150,
            mode="issue-to-pr",
            base_sha="base-sha",
            branch="autodev/issue-150",
            role_snapshots={},
        )
        return repo

    @staticmethod
    def _decision(
        role: str = "planner",
        route: str = "openai/gpt-example",
        *,
        provider: str = "openai-opencode",
    ) -> privacy.PrivacyDecision:
        model = route.partition("/")[2]
        return privacy.PrivacyDecision(
            outcome="CONSENT_REQUIRED",
            role=role,
            route=route,
            provider=provider,
            model=model,
            route_scope="direct-cloud",
            training="unknown",
            retention="unknown",
            policy_source="unknown or stale policy",
            enforcement_state="enforced-by-provider-contract",
            reason="training policy is unknown; customer-content retention is unknown",
        )

    def _environment(self, root: str):
        return mock.patch.dict(
            os.environ,
            {
                privacy_grant_contract.STORE_ENV: str(Path(root) / "privacy-grants.json"),
                privacy_grant_contract.REPOSITORY_ID_ENV: "github:owner/repo",
            },
            clear=False,
        )

    @staticmethod
    def _change_run_id(repo: Path, value: str) -> None:
        path = repo / workflow_stages.CURRENT_DIR / run_manifest.MANIFEST_NAME
        manifest = run_manifest.load_manifest(path)
        manifest["run_id"] = value
        run_manifest.save_manifest(path, manifest)

    def test_timed_grants_survive_distinct_runs_until_expiry(self):
        for duration, delta in (
            ("24h", timedelta(hours=24)),
            ("7d", timedelta(days=7)),
            ("30d", timedelta(days=30)),
        ):
            with self.subTest(duration=duration), tempfile.TemporaryDirectory() as temp_dir:
                repo = self._repo(temp_dir)
                policy = privacy.load_policy(repo)
                decision = self._decision()
                granted_at = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)
                with self._environment(temp_dir):
                    privacy_grant_commands.create_grant(
                        repo,
                        policy,
                        [decision],
                        duration=duration,
                        now=granted_at,
                    )
                    self._change_run_id(repo, "different-run")
                    self.assertIsNotNone(
                        privacy_grant_matching.matching_grant(
                            repo,
                            policy,
                            self._decision(),
                            now=granted_at + delta - timedelta(seconds=1),
                        )
                    )
                    self.assertIsNone(
                        privacy_grant_matching.matching_grant(
                            repo,
                            policy,
                            self._decision(),
                            now=granted_at + delta,
                        )
                    )

    def test_until_revoked_grant_is_immediately_revocable(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self._repo(temp_dir)
            policy = privacy.load_policy(repo)
            decision = self._decision()
            with self._environment(temp_dir):
                record = privacy_grant_commands.create_grant(
                    repo,
                    policy,
                    [decision],
                    duration="until-revoked",
                )
                self.assertIsNotNone(
                    privacy_grant_matching.matching_grant(repo, policy, self._decision())
                )
                self.assertEqual(
                    privacy_grant_commands.revoke_grants(repo, grant_id=str(record["id"])),
                    1,
                )
                self.assertIsNone(
                    privacy_grant_matching.matching_grant(repo, policy, self._decision())
                )

    def test_exact_route_grant_does_not_authorize_another_route(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self._repo(temp_dir)
            policy = privacy.load_policy(repo)
            with self._environment(temp_dir):
                privacy_grant_commands.create_grant(
                    repo,
                    policy,
                    [self._decision("planner", "openai/gpt-a")],
                    duration="7d",
                    scope="exact",
                )
                self.assertIsNotNone(
                    privacy_grant_matching.matching_grant(
                        repo, policy, self._decision("planner", "openai/gpt-a")
                    )
                )
                self.assertIsNone(
                    privacy_grant_matching.matching_grant(
                        repo, policy, self._decision("planner", "openai/gpt-b")
                    )
                )
                self.assertIsNone(
                    privacy_grant_matching.matching_grant(
                        repo,
                        policy,
                        self._decision(
                            "planner", "groq/gpt-a", provider="groq-opencode"
                        ),
                    )
                )

    def test_configured_scope_does_not_silently_widen_to_new_route(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self._repo(temp_dir)
            policy = privacy.load_policy(repo)
            with self._environment(temp_dir):
                privacy_grant_commands.create_grant(
                    repo,
                    policy,
                    [
                        self._decision("planner", "openai/planner"),
                        self._decision("implementer", "openai/implementer"),
                    ],
                    duration="30d",
                    scope="configured",
                )
                self.assertIsNotNone(
                    privacy_grant_matching.matching_grant(
                        repo,
                        policy,
                        self._decision("implementer", "openai/implementer"),
                    )
                )
                self.assertIsNone(
                    privacy_grant_matching.matching_grant(
                        repo,
                        policy,
                        self._decision("fixer", "openai/fixer"),
                    )
                )

    def test_provider_scope_allows_same_policy_provider_but_not_another_provider(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self._repo(temp_dir)
            policy = privacy.load_policy(repo)
            with self._environment(temp_dir):
                privacy_grant_commands.create_grant(
                    repo,
                    policy,
                    [self._decision("planner", "openai/gpt-a")],
                    duration="7d",
                    scope="provider",
                )
                self.assertIsNotNone(
                    privacy_grant_matching.matching_grant(
                        repo,
                        policy,
                        self._decision("fixer", "openai/gpt-b"),
                    )
                )
                self.assertIsNone(
                    privacy_grant_matching.matching_grant(
                        repo,
                        policy,
                        self._decision(
                            "fixer", "groq/gpt-b", provider="groq-opencode"
                        ),
                    )
                )

    def test_policy_or_enforcement_drift_invalidates_grant(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self._repo(temp_dir)
            policy = privacy.load_policy(repo)
            original = self._decision()
            with self._environment(temp_dir):
                privacy_grant_commands.create_grant(
                    repo,
                    policy,
                    [original],
                    duration="30d",
                )
                retention_drift = self._decision()
                retention_drift.retention = "bounded"
                self.assertIsNone(
                    privacy_grant_matching.matching_grant(repo, policy, retention_drift)
                )
                enforcement_drift = self._decision()
                enforcement_drift.enforcement_state = "unverified"
                self.assertIsNone(
                    privacy_grant_matching.matching_grant(repo, policy, enforcement_drift)
                )
                weaker_policy = privacy.PrivacyPolicy(
                    profile="no-training",
                    consent_mode="explicit",
                    source="test",
                )
                self.assertIsNone(
                    privacy_grant_matching.matching_grant(repo, weaker_policy, self._decision())
                )

    def test_forbidden_repository_policies_cannot_create_grants(self):
        for profile, consent_mode in (
            ("local-only", "explicit"),
            ("strict-confidential", "deny"),
        ):
            with self.subTest(profile=profile, consent_mode=consent_mode), tempfile.TemporaryDirectory() as temp_dir:
                repo = self._repo(
                    temp_dir, profile=profile, consent_mode=consent_mode
                )
                policy = privacy.load_policy(repo)
                with self._environment(temp_dir):
                    with self.assertRaises(privacy.PrivacyError):
                        privacy_grant_commands.create_grant(
                            repo,
                            policy,
                            [self._decision()],
                            duration="24h",
                        )

    def test_valid_grant_short_circuits_gate_and_audits_reference(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self._repo(temp_dir)
            policy = privacy.load_policy(repo)
            with self._environment(temp_dir):
                record = privacy_grant_commands.create_grant(
                    repo,
                    policy,
                    [self._decision()],
                    duration="7d",
                )
                original = privacy._consent_or_block

                def unexpected_gate(*args, **kwargs):
                    raise AssertionError("underlying consent gate was invoked")

                try:
                    privacy._consent_or_block = unexpected_gate
                    privacy_grant_hooks._install_privacy_gate()
                    result = privacy._consent_or_block(
                        repo, policy, self._decision(), None
                    )
                finally:
                    privacy._consent_or_block = original

                self.assertEqual(result.outcome, "ALLOW")
                self.assertEqual(result.enforcement_state, "user-consented")
                audit_path = (
                    repo
                    / workflow_stages.CURRENT_DIR
                    / privacy.PRIVACY_AUDIT
                )
                audit = audit_path.read_text(encoding="utf-8")
                self.assertIn("persistent-consent-use", audit)
                self.assertIn(str(record["id"]), audit)
                self.assertIn(str(record["expires_at"]), audit)

    def test_expired_grant_falls_through_to_fail_closed_gate(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self._repo(temp_dir)
            policy = privacy.load_policy(repo)
            old = datetime(2026, 1, 1, tzinfo=timezone.utc)
            with self._environment(temp_dir):
                privacy_grant_commands.create_grant(
                    repo,
                    policy,
                    [self._decision()],
                    duration="24h",
                    now=old,
                )
                self.assertIsNone(
                    privacy_grant_matching.matching_grant(repo, policy, self._decision())
                )

    def test_headless_run_hook_consumes_valid_grant_without_run_approval(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self._repo(temp_dir)
            policy = privacy.load_policy(repo)
            decision = self._decision("planner", "openai/planner")
            with self._environment(temp_dir):
                privacy_grant_commands.create_grant(
                    repo,
                    policy,
                    [decision],
                    duration="30d",
                )
                original = privacy_consent.ensure_run_consent
                try:
                    with mock.patch(
                        "automation.privacy_consent._known_consent_requirements",
                        return_value=[decision],
                    ):
                        privacy_grant_hooks._install_run_consent_hook()
                        privacy_consent.ensure_run_consent(
                            repo, {}, executable="opencode"
                        )
                finally:
                    privacy_consent.ensure_run_consent = original

                self.assertEqual(privacy_consent._load_ledger(repo), {})
                self.assertEqual(len(privacy_grant_commands.current_grants(repo)), 1)

    def test_headless_run_without_grant_cannot_manufacture_one(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self._repo(temp_dir)
            decision = self._decision("planner", "openai/planner")
            non_tty = SimpleNamespace(isatty=lambda: False)
            with self._environment(temp_dir):
                original = privacy_consent.ensure_run_consent
                try:
                    with (
                        mock.patch(
                            "automation.privacy_consent._known_consent_requirements",
                            return_value=[decision],
                        ),
                        mock.patch("automation.privacy_consent.sys.stdin", non_tty),
                        mock.patch("automation.privacy_grant_hooks.sys.stdin", non_tty),
                    ):
                        privacy_grant_hooks._install_run_consent_hook()
                        with self.assertRaises(privacy.PrivacyError):
                            privacy_consent.ensure_run_consent(
                                repo, {}, executable="opencode"
                            )
                finally:
                    privacy_consent.ensure_run_consent = original

                self.assertEqual(privacy_grant_commands.current_grants(repo), [])

    def test_interactive_run_prompt_can_create_30_day_grant(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self._repo(temp_dir)
            decision = self._decision("planner", "openai/planner")
            with self._environment(temp_dir):
                original = privacy_consent.ensure_run_consent
                try:
                    with (
                        mock.patch(
                            "automation.privacy_consent._known_consent_requirements",
                            return_value=[decision],
                        ),
                        mock.patch(
                            "automation.privacy_grant_hooks._read_run_choice",
                            return_value="30",
                        ),
                    ):
                        privacy_grant_hooks._install_run_consent_hook()
                        privacy_consent.ensure_run_consent(
                            repo, {}, executable="opencode"
                        )
                finally:
                    privacy_consent.ensure_run_consent = original

                grants = privacy_grant_commands.current_grants(repo)
                self.assertEqual(len(grants), 1)
                self.assertEqual(grants[0]["duration"], "30d")
                self.assertEqual(grants[0]["scope"], "configured-routes")
                self.assertEqual(privacy_consent._load_ledger(repo), {})

    def test_privacy_consent_cli_refuses_headless_creation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self._repo(temp_dir)
            non_tty = SimpleNamespace(isatty=lambda: False)
            with (
                self._environment(temp_dir),
                mock.patch("automation.privacy_grant_cli.sys.stdin", non_tty),
            ):
                result = privacy_grant_cli.run_cli(
                    ["consent", "--duration", "30d"], repo=repo
                )
                self.assertEqual(result, 2)
                self.assertEqual(privacy_grant_commands.current_grants(repo), [])

    def test_store_is_secret_free_and_revoke_all_is_immediate(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self._repo(temp_dir)
            policy = privacy.load_policy(repo)
            with self._environment(temp_dir):
                privacy_grant_commands.create_grant(
                    repo,
                    policy,
                    [self._decision("planner", "openai/planner")],
                    duration="24h",
                )
                privacy_grant_commands.create_grant(
                    repo,
                    policy,
                    [self._decision("fixer", "openai/fixer")],
                    duration="7d",
                )
                raw = Path(os.environ[privacy_grant_contract.STORE_ENV]).read_text(
                    encoding="utf-8"
                )
                self.assertNotIn("prompt", raw.casefold())
                self.assertNotIn("api_key", raw.casefold())
                self.assertNotIn("secret", raw.casefold())
                self.assertEqual(
                    privacy_grant_commands.revoke_grants(repo, revoke_all=True), 2
                )
                self.assertTrue(
                    all(
                        item["status"] == "revoked"
                        for item in privacy_grant_commands.current_grants(repo)
                    )
                )


if __name__ == "__main__":
    unittest.main()
