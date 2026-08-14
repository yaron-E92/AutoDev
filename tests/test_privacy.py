import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from automation import privacy
from automation.model_providers import ModelConfig, ModelProvider, ProviderResponse
from automation.model_roles import ModelInvocationError, invoke_model


class _RecordingProvider(ModelProvider):
    def __init__(self):
        self.called = False
        self.request_options = {}

    def invoke(self, prompt: str, *, model: str, timeout_seconds: int) -> ProviderResponse:
        self.called = True
        return ProviderResponse("ok", {})


class PrivacyTests(unittest.TestCase):
    def _repo(self, root: str, *, profile: str | None = None, consent_mode: str = "explicit") -> Path:
        repo = Path(root)
        (repo / ".git").mkdir()
        if profile is not None:
            config = repo / ".autodev" / "privacy.json"
            config.parent.mkdir()
            config.write_text(
                json.dumps({"profile": profile, "consent_mode": consent_mode}),
                encoding="utf-8",
            )
        return repo

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
            repo = self._repo(temp_dir)
            provider = _RecordingProvider()
            config = ModelConfig(
                provider="openai-compatible-chat-completions",
                model="vendor/model",
                base_url="https://openrouter.ai/api/v1",
            )
            decision = privacy.authorize_direct_call(provider, config, role="planner", repo=repo)

        self.assertEqual(decision.outcome, "ALLOW")
        self.assertEqual(decision.enforcement_state, "verified-effective")
        self.assertEqual(
            provider.request_options["provider"],
            {"data_collection": "deny", "zdr": True},
        )

    def test_openrouter_controls_are_applied_to_headroom_direct_and_proxy_paths(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self._repo(temp_dir)
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
            with self.assertRaises(ModelInvocationError) as raised:
                invoke_model(provider, config, "SECRET REPOSITORY PROMPT", role="planner", repo=repo)

        self.assertFalse(provider.called)
        self.assertEqual(raised.exception.classification, "privacy_blocked")
        self.assertNotIn("SECRET REPOSITORY PROMPT", json.dumps(raised.exception.record))

    def test_no_training_profile_allows_groq_but_strict_requires_consent(self):
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
            repo = self._repo(temp_dir)
            calls = []

            def runner(command, **kwargs):
                calls.append(kwargs.get("env", {}))
                raw = kwargs.get("env", {}).get("OPENCODE_CONFIG_CONTENT", "")
                if raw:
                    resolved = json.loads(raw)
                else:
                    resolved = {
                        "provider": {
                            "openrouter": {
                                "models": {"vendor/model": {}}
                            }
                        }
                    }
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
            repo = self._repo(temp_dir)

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
            repo = self._repo(temp_dir)
            calls = 0

            def runner(command, **kwargs):
                nonlocal calls
                calls += 1
                # Simulate a higher-precedence/managed config stripping the attempted runtime overlay.
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
            repo = self._repo(temp_dir)
            provider = _RecordingProvider()
            config = ModelConfig(
                provider="openai-compatible-chat-completions",
                model="vendor/model",
                base_url="https://openrouter.ai/api/v1",
            )
            invoke_model(provider, config, "TOP SECRET PROMPT", role="planner", repo=repo)
            audit = (repo / ".autodev-run" / privacy.PRIVACY_AUDIT).read_text(encoding="utf-8")

        self.assertIn("verified-effective", audit)
        self.assertNotIn("TOP SECRET PROMPT", audit)
        self.assertNotIn("Authorization", audit)


if __name__ == "__main__":
    unittest.main()
