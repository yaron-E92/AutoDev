import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from automation import privacy
from automation.provider_contract import ModelConfig, ModelProvider, ProviderResponse


class _RecordingProvider(ModelProvider):
    def __init__(self):
        self.called = False
        self.request_options = {}

    def invoke(self, prompt: str, *, model: str, timeout_seconds: int) -> ProviderResponse:
        self.called = True
        return ProviderResponse("ok", {})


class PrivacyTests(unittest.TestCase):
    def _repo(
        self,
        root: str,
        *,
        profile: str | None = None,
        consent_mode: str = "explicit",
        provider_attestations: dict | None = None,
    ) -> Path:
        repo = Path(root)
        (repo / ".git").mkdir()
        if profile is not None or provider_attestations is not None:
            config = repo / ".autodev" / "privacy.json"
            config.parent.mkdir()
            payload = {
                "profile": profile or "strict-confidential",
                "consent_mode": consent_mode,
            }
            if provider_attestations is not None:
                payload["provider_attestations"] = provider_attestations
            config.write_text(json.dumps(payload), encoding="utf-8")
        return repo

    @staticmethod
    def _openrouter_attestation() -> dict:
        return {
            "openrouter": {
                "checked_at": privacy.POLICY_REVIEWED_AT,
                "use_inputs_outputs": "disabled",
                "prompt_logging": "disabled",
            }
        }

    def test_real_repository_defaults_to_strict_confidential(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self._repo(temp_dir)
            policy = privacy.load_policy(repo)

        self.assertEqual(policy.profile, "strict-confidential")
        self.assertTrue(policy.no_training)
        self.assertTrue(policy.zero_retention)

    def test_environment_may_strengthen_but_not_weaken_repository_policy(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self._repo(temp_dir, profile="strict-confidential")
            with mock.patch.dict("os.environ", {"AUTODEV_PRIVACY_PROFILE": "off"}):
                self.assertEqual(privacy.load_policy(repo).profile, "strict-confidential")
            with mock.patch.dict("os.environ", {"AUTODEV_PRIVACY_PROFILE": "local-only"}):
                self.assertEqual(privacy.load_policy(repo).profile, "local-only")

    def test_direct_openrouter_request_is_hardened_and_verified_before_use(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self._repo(
                temp_dir,
                provider_attestations=self._openrouter_attestation(),
            )
            provider = _RecordingProvider()
            config = ModelConfig(
                provider="openai-compatible-chat-completions",
                model="openai/gpt-example",
                base_url="https://openrouter.ai/api/v1",
            )
            decision = privacy.authorize_direct_call(provider, config, role="planner", repo=repo)

        self.assertEqual(decision.outcome, "ALLOW")
        self.assertEqual(decision.provider, "openrouter")
        self.assertIn("request-verified", decision.enforcement_state)
        self.assertIn("account-attested", decision.enforcement_state)
        self.assertEqual(
            provider.request_options["provider"],
            {"data_collection": "deny", "zdr": True},
        )

    def test_openrouter_request_controls_are_not_enough_if_account_logging_is_unverified(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self._repo(temp_dir, consent_mode="deny")
            provider = _RecordingProvider()
            config = ModelConfig(
                provider="openai-compatible-chat-completions",
                model="vendor/model",
                base_url="https://openrouter.ai/api/v1",
            )
            with self.assertRaises(privacy.PrivacyError):
                privacy.authorize_direct_call(provider, config, role="planner", repo=repo)

        self.assertEqual(
            provider.request_options["provider"],
            {"data_collection": "deny", "zdr": True},
        )
        self.assertFalse(provider.called)

    def test_openrouter_controls_are_applied_to_headroom_direct_and_proxy_paths(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self._repo(
                temp_dir,
                provider_attestations=self._openrouter_attestation(),
            )
            direct = SimpleNamespace(request_options={})
            proxy = SimpleNamespace(request_options={})
            wrapped = SimpleNamespace(direct_provider=direct, proxy_provider=proxy)
            config = ModelConfig(
                provider="openai-compatible-chat-completions",
                model="vendor/model",
                base_url="https://openrouter.ai/api/v1",
            )
            decision = privacy.authorize_direct_call(wrapped, config, role="implementer", repo=repo)

        self.assertEqual(decision.outcome, "ALLOW")
        expected = {"data_collection": "deny", "zdr": True}
        self.assertEqual(direct.request_options["provider"], expected)
        self.assertEqual(proxy.request_options["provider"], expected)

    def test_blocked_direct_call_sends_zero_prompt_bytes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self._repo(temp_dir, consent_mode="deny")
            provider = _RecordingProvider()
            config = ModelConfig(
                provider="openai-compatible-chat-completions",
                model="m",
                base_url="https://unknown.example/v1",
            )
            with self.assertRaises(privacy.PrivacyError):
                privacy.authorize_direct_call(provider, config, role="planner", repo=repo)

        self.assertFalse(provider.called)

    def test_no_training_profile_allows_groq_but_strict_requires_consent_without_zdr_attestation(self):
        config = ModelConfig(
            provider="openai-compatible-chat-completions",
            model="groq/model",
            base_url="https://api.groq.com/openai/v1",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            no_training = self._repo(temp_dir, profile="no-training")
            decision = privacy.authorize_direct_call(
                _RecordingProvider(), config, role="reader", repo=no_training
            )
            self.assertEqual(decision.outcome, "ALLOW")

        with tempfile.TemporaryDirectory() as temp_dir:
            strict = self._repo(temp_dir, profile="strict-confidential")
            with self.assertRaises(privacy.PrivacyError):
                privacy.authorize_direct_call(
                    _RecordingProvider(),
                    config,
                    role="reader",
                    repo=strict,
                    consent_reader=lambda _: "no",
                )
            decision = privacy.authorize_direct_call(
                _RecordingProvider(),
                config,
                role="reader",
                repo=strict,
                consent_reader=lambda _: "yes",
            )
            self.assertEqual(decision.enforcement_state, "user-consented")

    def test_fresh_groq_zdr_attestation_allows_strict_route(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self._repo(
                temp_dir,
                provider_attestations={
                    "groq": {
                        "checked_at": privacy.POLICY_REVIEWED_AT,
                        "zero_data_retention": "enabled",
                    }
                },
            )
            config = ModelConfig(
                provider="openai-compatible-chat-completions",
                model="groq/model",
                base_url="https://api.groq.com/openai/v1",
            )
            decision = privacy.authorize_direct_call(
                _RecordingProvider(), config, role="reader", repo=repo
            )

        self.assertEqual(decision.retention, "zero")
        self.assertIn("account-attested", decision.enforcement_state)

    def test_stale_provider_attestation_does_not_satisfy_strict_route(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self._repo(
                temp_dir,
                consent_mode="deny",
                provider_attestations={
                    "groq": {
                        "checked_at": "2020-01-01",
                        "zero_data_retention": "enabled",
                    }
                },
            )
            config = ModelConfig(
                provider="openai-compatible-chat-completions",
                model="groq/model",
                base_url="https://api.groq.com/openai/v1",
            )
            with self.assertRaises(privacy.PrivacyError):
                privacy.authorize_direct_call(
                    _RecordingProvider(), config, role="reader", repo=repo
                )

    def test_local_only_does_not_offer_cloud_consent_override(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self._repo(temp_dir, profile="local-only")
            config = ModelConfig(
                provider="openai-compatible-chat-completions",
                model="groq/model",
                base_url="https://api.groq.com/openai/v1",
            )
            with self.assertRaises(privacy.PrivacyError):
                privacy.authorize_direct_call(
                    _RecordingProvider(),
                    config,
                    role="reader",
                    repo=repo,
                    consent_reader=lambda _: "yes",
                )

    def test_local_ollama_and_ollama_cloud_are_classified_differently(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self._repo(temp_dir)
            local = ModelConfig(provider="command", model="qwen3", command="ollama run qwen3")
            cloud = ModelConfig(
                provider="command",
                model="nemotron-3-super:cloud",
                command="ollama run nemotron-3-super:cloud",
            )
            local_decision = privacy.authorize_direct_call(
                _RecordingProvider(), local, role="reader", repo=repo
            )
            cloud_decision = privacy.authorize_direct_call(
                _RecordingProvider(), cloud, role="reader", repo=repo
            )

        self.assertEqual(local_decision.route_scope, "local")
        self.assertEqual(cloud_decision.provider, "ollama-cloud")
        self.assertEqual(cloud_decision.retention, "zero")
        self.assertEqual(cloud_decision.training, "denied")

    def test_opencode_openrouter_overlay_is_resolved_and_verified_before_role(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self._repo(
                temp_dir,
                provider_attestations=self._openrouter_attestation(),
            )
            calls = []

            def runner(command, **kwargs):
                calls.append(kwargs.get("env", {}))
                raw = kwargs.get("env", {}).get("OPENCODE_CONFIG_CONTENT", "")
                resolved = (
                    json.loads(raw)
                    if raw
                    else {"provider": {"openrouter": {"models": {"vendor/model": {}}}}}
                )
                return SimpleNamespace(returncode=0, stdout=json.dumps(resolved), stderr="")

            decision, env = privacy.authorize_opencode_role(
                repo,
                role="implementer",
                model="openrouter/vendor/model",
                opencode_cli="opencode",
                runner=runner,
            )

        self.assertEqual(decision.outcome, "ALLOW")
        inline = json.loads(env["OPENCODE_CONFIG_CONTENT"])
        controls = inline["provider"]["openrouter"]["models"]["vendor/model"]["options"]["provider"]
        self.assertEqual(controls, {"data_collection": "deny", "zdr": True})
        self.assertEqual(len(calls), 2)

    def test_opencode_v2_overlay_uses_provider_body(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self._repo(
                temp_dir,
                provider_attestations=self._openrouter_attestation(),
            )

            def runner(command, **kwargs):
                raw = kwargs.get("env", {}).get("OPENCODE_CONFIG_CONTENT", "")
                resolved = json.loads(raw) if raw else {"providers": {"openrouter": {"models": {}}}}
                return SimpleNamespace(returncode=0, stdout=json.dumps(resolved), stderr="")

            decision, env = privacy.authorize_opencode_role(
                repo,
                role="planner",
                model="openrouter/vendor/model",
                opencode_cli="opencode",
                runner=runner,
            )

        self.assertEqual(decision.outcome, "ALLOW")
        self.assertEqual(
            json.loads(env["OPENCODE_CONFIG_CONTENT"])["providers"]["openrouter"]["body"]["provider"],
            {"data_collection": "deny", "zdr": True},
        )

    def test_opencode_control_that_is_not_effective_falls_back_to_consent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self._repo(
                temp_dir,
                provider_attestations=self._openrouter_attestation(),
            )
            calls = 0

            def runner(command, **kwargs):
                nonlocal calls
                calls += 1
                resolved = {
                    "provider": {
                        "openrouter": {
                            "models": {
                                "vendor/model": {
                                    "options": {"provider": {"data_collection": "allow"}}
                                }
                            }
                        }
                    }
                }
                return SimpleNamespace(returncode=0, stdout=json.dumps(resolved), stderr="")

            with self.assertRaises(privacy.PrivacyError):
                privacy.authorize_opencode_role(
                    repo,
                    role="planner",
                    model="openrouter/vendor/model",
                    opencode_cli="opencode",
                    runner=runner,
                    consent_reader=lambda _: "no",
                )

        self.assertEqual(calls, 2)

    def test_opencode_openai_is_not_assumed_to_have_api_business_policy(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self._repo(temp_dir, profile="no-training", consent_mode="deny")
            with self.assertRaises(privacy.PrivacyError):
                privacy.authorize_opencode_role(
                    repo,
                    role="planner",
                    model="openai/gpt-example",
                    opencode_cli="opencode",
                    runner=lambda *args, **kwargs: None,
                )

    def test_exact_headless_consent_is_narrowly_scoped(self):
        config = ModelConfig(
            provider="openai-compatible-chat-completions",
            model="groq/model",
            base_url="https://api.groq.com/openai/v1",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self._repo(temp_dir)
            route = "groq/groq/model"
            with mock.patch.dict("os.environ", {"AUTODEV_PRIVACY_CONSENT": f"planner={route}"}):
                allowed = privacy.authorize_direct_call(
                    _RecordingProvider(), config, role="planner", repo=repo
                )
                with self.assertRaises(privacy.PrivacyError):
                    privacy.authorize_direct_call(
                        _RecordingProvider(), config, role="implementer", repo=repo
                    )

        self.assertEqual(allowed.enforcement_state, "user-consented")

    def test_audit_contains_policy_metadata_but_not_prompt_content(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self._repo(
                temp_dir,
                provider_attestations=self._openrouter_attestation(),
            )
            provider = _RecordingProvider()
            config = ModelConfig(
                provider="openai-compatible-chat-completions",
                model="vendor/model",
                base_url="https://openrouter.ai/api/v1",
            )
            privacy.authorize_direct_call(provider, config, role="planner", repo=repo)
            provider.invoke("TOP SECRET PROMPT", model=config.model, timeout_seconds=config.timeout_seconds)
            audit = (repo / ".autodev-run" / privacy.PRIVACY_AUDIT).read_text(encoding="utf-8")

        self.assertIn("request-verified", audit)
        self.assertIn("account-attested", audit)
        self.assertNotIn("TOP SECRET PROMPT", audit)
        self.assertNotIn("Authorization", audit)


if __name__ == "__main__":
    unittest.main()
