from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from automation import privacy, privacy_consent, run_manifest, workflow_stages


class _FakeTTY(io.StringIO):
    def isatty(self) -> bool:
        return True


class PrivacyConsentTtyTests(unittest.TestCase):
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
            issue_number=132,
            mode="issue-to-pr",
            base_sha="base-sha",
            branch="fix/opencode-privacy-consent-tty",
            role_snapshots={},
        )
        return repo

    @staticmethod
    def _decision(
        role: str = "planner", route: str = "openai/planner"
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

    def test_captured_stdio_uses_opted_in_controlling_terminal_for_batch(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self._repo(temp_dir)
            required = [self._decision()]
            non_tty = SimpleNamespace(isatty=lambda: False)
            with (
                mock.patch(
                    "automation.privacy_consent._known_consent_requirements",
                    return_value=required,
                ),
                mock.patch("automation.privacy_consent.sys.stdin", non_tty),
                mock.patch(
                    "automation.privacy_consent._read_run_choice_from_controlling_terminal",
                    return_value="a",
                ) as terminal_reader,
                mock.patch.dict(
                    "os.environ",
                    {
                        privacy_consent.INTERACTIVE_CONSENT_ENV:
                            privacy_consent.INTERACTIVE_CONSENT_VALUE
                    },
                    clear=False,
                ),
            ):
                privacy_consent.ensure_run_consent(repo, {}, executable="opencode")

            self.assertEqual(
                privacy_consent._load_ledger(repo)["interaction_mode"], "batch"
            )
            terminal_reader.assert_called_once_with(required)

    def test_headless_run_without_bridge_opt_in_still_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self._repo(temp_dir)
            required = [self._decision()]
            non_tty = SimpleNamespace(isatty=lambda: False)
            env = dict(__import__("os").environ)
            env.pop(privacy_consent.INTERACTIVE_CONSENT_ENV, None)
            env.pop("AUTODEV_PRIVACY_CONSENT", None)
            with (
                mock.patch(
                    "automation.privacy_consent._known_consent_requirements",
                    return_value=required,
                ),
                mock.patch("automation.privacy_consent.sys.stdin", non_tty),
                mock.patch.dict("os.environ", env, clear=True),
                mock.patch(
                    "automation.privacy_consent._read_run_choice_from_controlling_terminal"
                ) as terminal_reader,
            ):
                with self.assertRaises(privacy.PrivacyError):
                    privacy_consent.ensure_run_consent(repo, {}, executable="opencode")

            terminal_reader.assert_called_once_with(required)

    def test_bridge_opt_in_without_controlling_terminal_still_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self._repo(temp_dir)
            required = [self._decision()]
            non_tty = SimpleNamespace(isatty=lambda: False)
            with (
                mock.patch(
                    "automation.privacy_consent._known_consent_requirements",
                    return_value=required,
                ),
                mock.patch("automation.privacy_consent.sys.stdin", non_tty),
                mock.patch(
                    "automation.privacy_consent._read_run_choice_from_controlling_terminal",
                    return_value=None,
                ),
                mock.patch.dict(
                    "os.environ",
                    {
                        privacy_consent.INTERACTIVE_CONSENT_ENV:
                            privacy_consent.INTERACTIVE_CONSENT_VALUE
                    },
                    clear=False,
                ),
            ):
                with self.assertRaises(privacy.PrivacyError):
                    privacy_consent.ensure_run_consent(repo, {}, executable="opencode")

    def test_per_call_review_uses_controlling_terminal_reader(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self._repo(temp_dir)
            policy = privacy.load_policy(repo)
            decision = self._decision()
            privacy_consent._save_ledger(
                repo,
                {
                    "interaction_mode": "per-call",
                    "created_at": "now",
                    "approvals": [],
                },
            )
            original = privacy._consent_or_block
            try:
                privacy_consent._install_consent_gate()
                with (
                    mock.patch.dict(
                        "os.environ",
                        {
                            privacy_consent.INTERACTIVE_CONSENT_ENV:
                                privacy_consent.INTERACTIVE_CONSENT_VALUE
                        },
                        clear=False,
                    ),
                    mock.patch(
                        "automation.privacy_consent._read_call_consent_from_controlling_terminal",
                        return_value="yes",
                    ) as terminal_reader,
                ):
                    result = privacy._consent_or_block(repo, policy, decision, None)
            finally:
                privacy._consent_or_block = original

            self.assertEqual(result.outcome, "ALLOW")
            self.assertEqual(result.consent_scope, "this call")
            terminal_reader.assert_called_once()
            approvals = privacy_consent._approvals(privacy_consent._load_ledger(repo))
            self.assertEqual(len(approvals), 1)
            self.assertEqual(approvals[0]["mode"], "per-call")

    def test_posix_controlling_terminal_uses_dev_tty(self):
        tty = _FakeTTY("a\n")
        with (
            mock.patch.dict(
                "os.environ",
                {
                    privacy_consent.INTERACTIVE_CONSENT_ENV:
                        privacy_consent.INTERACTIVE_CONSENT_VALUE
                },
                clear=False,
            ),
            mock.patch.object(privacy_consent.os, "name", "posix"),
            mock.patch("builtins.open", return_value=tty) as opened,
        ):
            with privacy_consent._controlling_terminal() as console:
                self.assertIsNotNone(console)

        self.assertEqual(opened.call_args.args[0], "/dev/tty")

    def test_windows_controlling_terminal_uses_console_devices(self):
        reader = _FakeTTY("a\n")
        writer = _FakeTTY()
        with (
            mock.patch.dict(
                "os.environ",
                {
                    privacy_consent.INTERACTIVE_CONSENT_ENV:
                        privacy_consent.INTERACTIVE_CONSENT_VALUE
                },
                clear=False,
            ),
            mock.patch.object(privacy_consent.os, "name", "nt"),
            mock.patch("builtins.open", side_effect=[reader, writer]) as opened,
        ):
            with privacy_consent._controlling_terminal() as console:
                self.assertIsNotNone(console)

        self.assertEqual(opened.call_args_list[0].args[0], "CONIN$")
        self.assertEqual(opened.call_args_list[1].args[0], "CONOUT$")


if __name__ == "__main__":
    unittest.main()
