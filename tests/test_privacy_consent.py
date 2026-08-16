from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from automation import opencode_coordinator, privacy, privacy_consent, run_manifest, workflow_stages


class PrivacyConsentTests(unittest.TestCase):
    def _repo(self, root: str) -> Path:
        repo = Path(root)
        (repo / ".git").mkdir(parents=True)
        config = repo / ".autodev" / "privacy.json"
        config.parent.mkdir(parents=True)
        config.write_text(
            json.dumps({"profile": "strict-confidential", "consent_mode": "explicit"}),
            encoding="utf-8",
        )
        current = repo / workflow_stages.CURRENT_DIR
        current.mkdir(parents=True)
        run_manifest.create_manifest(
            current / run_manifest.MANIFEST_NAME,
            repo_path=repo,
            github_repo="owner/repo",
            issue_number=128,
            mode="issue-to-pr",
            base_sha="base-sha",
            branch="autodev/issue-128",
            role_snapshots={},
        )
        return repo

    @staticmethod
    def _decision(role: str = "planner", route: str = "openai/gpt-example") -> privacy.PrivacyDecision:
        provider = "openai-opencode" if route.startswith("openai/") else "unknown"
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

    def _originals(self) -> tuple[object, object, object]:
        return privacy._consent_or_block, privacy._audit, opencode_coordinator._run_agent_process

    @staticmethod
    def _restore(originals: tuple[object, object, object]) -> None:
        privacy._consent_or_block = originals[0]  # type: ignore[assignment]
        privacy._audit = originals[1]  # type: ignore[assignment]
        opencode_coordinator._run_agent_process = originals[2]  # type: ignore[assignment]

    def test_known_routes_include_conditional_fixer(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self._repo(temp_dir)
            seen: list[str] = []
            mappings = {
                role: {"model": f"openai/{role}-model"}
                for role in privacy_consent.ROLE_NAMES
            }

            def preview(repo, *, role, model, executable, runner):
                seen.append(role)
                return self._decision(role, model)

            with mock.patch("automation.privacy_consent._preview_decision", side_effect=preview):
                required = privacy_consent._known_consent_requirements(
                    repo,
                    mappings,
                    executable="opencode",
                    runner=lambda *args, **kwargs: None,
                )

        self.assertEqual(seen, list(privacy_consent.ROLE_NAMES))
        self.assertIn("fixer", [item.role for item in required])

    def test_batch_approval_reuses_exact_identities_without_reprompting(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self._repo(temp_dir)
            policy = privacy.load_policy(repo)
            required = [
                self._decision("planner", "openai/planner"),
                self._decision("implementer", "openai/implementer"),
                self._decision("fixer", "openai/fixer"),
            ]
            tty = SimpleNamespace(isatty=lambda: True)
            with (
                mock.patch("automation.privacy_consent._known_consent_requirements", return_value=required),
                mock.patch("automation.privacy_consent.sys.stdin", tty),
                mock.patch("builtins.input", return_value="a"),
            ):
                privacy_consent.ensure_run_consent(repo, {}, executable="opencode")

            ledger = privacy_consent._load_ledger(repo)
            self.assertEqual(ledger["interaction_mode"], "batch")
            self.assertEqual(len(privacy_consent._approvals(ledger)), 3)

            originals = self._originals()
            try:
                privacy_consent.install()
                result = privacy._consent_or_block(
                    repo,
                    policy,
                    self._decision("planner", "openai/planner"),
                    lambda _: self.fail("batch-approved route must not prompt again"),
                )
            finally:
                self._restore(originals)

            self.assertEqual(result.outcome, "ALLOW")
            self.assertEqual(result.consent_scope, "this run (batch consent)")

    def test_per_call_approval_is_persisted_and_reused_on_resume(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self._repo(temp_dir)
            policy = privacy.load_policy(repo)
            decision = self._decision("planner", "openai/planner")
            tty = SimpleNamespace(isatty=lambda: True)
            with (
                mock.patch("automation.privacy_consent._known_consent_requirements", return_value=[decision]),
                mock.patch("automation.privacy_consent.sys.stdin", tty),
                mock.patch("builtins.input", return_value="r"),
            ):
                privacy_consent.ensure_run_consent(repo, {}, executable="opencode")

            originals = self._originals()
            try:
                privacy_consent.install()
                first = privacy._consent_or_block(
                    repo,
                    policy,
                    self._decision("planner", "openai/planner"),
                    lambda _: "yes",
                )
                second = privacy._consent_or_block(
                    repo,
                    policy,
                    self._decision("planner", "openai/planner"),
                    lambda _: self.fail("unchanged per-call approval must survive resume/reuse"),
                )
            finally:
                self._restore(originals)

            self.assertEqual(first.outcome, "ALLOW")
            self.assertEqual(second.outcome, "ALLOW")
            self.assertEqual(second.consent_scope, "this run (per-call consent)")
            approvals = privacy_consent._approvals(privacy_consent._load_ledger(repo))
            self.assertEqual(len(approvals), 1)
            self.assertEqual(approvals[0]["mode"], "per-call")

    def test_route_or_policy_drift_requires_fresh_consent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self._repo(temp_dir)
            policy = privacy.load_policy(repo)
            original_decision = self._decision("planner", "openai/planner")
            privacy_consent._save_ledger(
                repo,
                {
                    "interaction_mode": "batch",
                    "created_at": "now",
                    "approvals": [
                        privacy_consent._approval_record(
                            repo, policy, original_decision, mode="batch"
                        )
                    ],
                },
            )

            originals = self._originals()
            prompts: list[str] = []
            try:
                privacy_consent.install()
                route_result = privacy._consent_or_block(
                    repo,
                    policy,
                    self._decision("planner", "openai/different-model"),
                    lambda text: prompts.append(text) or "yes",
                )
                drifted = self._decision("planner", "openai/planner")
                drifted.retention = "bounded"
                policy_result = privacy._consent_or_block(
                    repo,
                    policy,
                    drifted,
                    lambda text: prompts.append(text) or "yes",
                )
            finally:
                self._restore(originals)

            self.assertEqual(route_result.outcome, "ALLOW")
            self.assertEqual(policy_result.outcome, "ALLOW")
            self.assertEqual(len(prompts), 2)

    def test_new_run_does_not_reuse_old_batch_approval(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self._repo(temp_dir)
            policy = privacy.load_policy(repo)
            decision = self._decision()
            privacy_consent._save_ledger(
                repo,
                {
                    "interaction_mode": "batch",
                    "created_at": "now",
                    "approvals": [privacy_consent._approval_record(repo, policy, decision, mode="batch")],
                },
            )
            path = opencode_coordinator.opencode_resume.manifest_path(repo)
            manifest = run_manifest.load_manifest(path)
            manifest["run_id"] = "new-run-id"
            run_manifest.save_manifest(path, manifest)

            self.assertEqual(privacy_consent._load_ledger(repo), {})

    def test_denial_stops_before_any_role_call(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self._repo(temp_dir)
            tty = SimpleNamespace(isatty=lambda: True)
            with (
                mock.patch(
                    "automation.privacy_consent._known_consent_requirements",
                    return_value=[self._decision()],
                ),
                mock.patch("automation.privacy_consent.sys.stdin", tty),
                mock.patch("builtins.input", return_value="n"),
            ):
                with self.assertRaises(privacy.PrivacyError):
                    privacy_consent.ensure_run_consent(repo, {}, executable="opencode")

            self.assertEqual(
                privacy_consent._load_ledger(repo)["interaction_mode"],
                "denied",
            )

    def test_noninteractive_run_fails_closed_without_exact_environment_consent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self._repo(temp_dir)
            required = [self._decision("planner", "openai/planner")]
            non_tty = SimpleNamespace(isatty=lambda: False)
            with (
                mock.patch("automation.privacy_consent._known_consent_requirements", return_value=required),
                mock.patch("automation.privacy_consent.sys.stdin", non_tty),
                mock.patch.dict("os.environ", {}, clear=False),
            ):
                with self.assertRaises(privacy.PrivacyError):
                    privacy_consent.ensure_run_consent(repo, {}, executable="opencode")

    def test_noninteractive_exact_environment_consent_is_run_scoped(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self._repo(temp_dir)
            required = [self._decision("planner", "openai/planner")]
            non_tty = SimpleNamespace(isatty=lambda: False)
            with (
                mock.patch("automation.privacy_consent._known_consent_requirements", return_value=required),
                mock.patch("automation.privacy_consent.sys.stdin", non_tty),
                mock.patch.dict(
                    "os.environ",
                    {"AUTODEV_PRIVACY_CONSENT": "planner=openai/planner"},
                ),
            ):
                privacy_consent.ensure_run_consent(repo, {}, executable="opencode")

            ledger = privacy_consent._load_ledger(repo)
            self.assertEqual(ledger["interaction_mode"], "noninteractive-exact")
            self.assertEqual(privacy_consent._approvals(ledger)[0]["mode"], "environment")

    def test_audit_distinguishes_batch_from_per_call_consent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self._repo(temp_dir)
            policy = privacy.load_policy(repo)
            decision = self._decision("planner", "openai/planner")
            privacy_consent._save_ledger(
                repo,
                {
                    "interaction_mode": "batch",
                    "created_at": "now",
                    "approvals": [privacy_consent._approval_record(repo, policy, decision, mode="batch")],
                },
            )

            originals = self._originals()
            try:
                privacy_consent.install()
                privacy._consent_or_block(
                    repo,
                    policy,
                    self._decision("planner", "openai/planner"),
                    lambda _: "no",
                )
                privacy._consent_or_block(
                    repo,
                    policy,
                    self._decision("verifier", "openai/verifier"),
                    lambda _: "yes",
                )
            finally:
                self._restore(originals)

            audit = (repo / workflow_stages.CURRENT_DIR / privacy.PRIVACY_AUDIT).read_text(encoding="utf-8")
            self.assertIn("this run (batch consent)", audit)
            self.assertIn('"consent_scope": "this call"', audit)


if __name__ == "__main__":
    unittest.main()
