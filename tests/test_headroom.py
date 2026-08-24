import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib import error

from automation.headroom import (
    HeadroomConfig,
    HeadroomPromptResult,
    compressible_ranges,
    prepare_prompt,
    resolve_headroom_values,
)
from automation.model_providers import (
    HeadroomProvider,
    ModelConfig,
    ModelProvider,
    ProviderError,
    ProviderResponse,
    create_provider,
    model_config_from_values,
    proxy_headers,
    validate_safe_headers,
)
from automation.model_roles import invoke_model, resolve_role_configs
from automation.semantic_verifier import build_semantic_prompt


REPO_ROOT = Path(__file__).resolve().parents[1]


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class StubProvider(ModelProvider):
    def __init__(self, response="ok", failure=None):
        self.response = response
        self.failure = failure
        self.prompts = []

    def invoke(self, prompt, *, model, timeout_seconds):
        self.prompts.append(prompt)
        if self.failure is not None:
            raise self.failure
        return ProviderResponse(self.response, {"reported_model": model})


class HeadroomTests(unittest.TestCase):
    def test_global_and_role_overrides_default_verifier_to_disabled(self):
        profile = {
            "headroom": {
                "enabled": True,
                "proxy_url": "http://127.0.0.1:8787/v1",
                "roles": {"fixer": {"enabled": False}},
            }
        }

        self.assertTrue(resolve_headroom_values(profile, "implementer")["enabled"])
        self.assertFalse(resolve_headroom_values(profile, "fixer")["enabled"])
        self.assertFalse(resolve_headroom_values(profile, "verifier")["enabled"])

    def test_role_resolution_composes_headroom_with_version_two_provider_profile(self):
        configs = resolve_role_configs(
            defaults={
                "reader": {
                    "transport": "openai-compatible-chat-completions",
                    "model": "reader",
                    "base_url": "https://reader.invalid/v1",
                },
                "coder": {
                    "transport": "openai-compatible-chat-completions",
                    "model": "coder",
                    "base_url": "https://coder.invalid/v1",
                },
            },
            file_config={
                "version": 2,
                "headroom": {
                    "enabled": True,
                    "proxy_url": "http://127.0.0.1:8787/v1",
                },
                "roles": {
                    "reader": {"model": "reader"},
                    "implementer": {"model": "implementer"},
                    "fixer": {"model": "fixer"},
                    "verifier": {"model": "verifier"},
                },
            },
        )

        self.assertTrue(configs["implementer"].headroom.enabled)
        self.assertTrue(configs["fixer"].headroom.enabled)
        self.assertFalse(configs["verifier"].headroom.enabled)


    def test_semantic_compression_preserves_issue_acceptance_criteria_and_json_schema(self):
        template = (REPO_ROOT / "promptTemplates" / "verifier.md").read_text(encoding="utf-8")
        prompt = build_semantic_prompt(
            issue_text="# Issue\n\n## Acceptance criteria\n- EXACT CRITERION",
            synthesized_handoff="supporting handoff",
            plan="supporting plan",
            changed_files=["src/a.py"],
            diff="diff --git a/src/a.py b/src/a.py",
            deterministic_evidence="tests passed",
            uncertainty_notes="none",
            template=template,
        )
        ranges = compressible_ranges(prompt, "verifier")

        def fake_urlopen(req, timeout):
            return FakeResponse(
                {
                    "messages": [
                        {"role": "user", "content": f"C{index}"}
                        for index in range(len(ranges))
                    ]
                }
            )

        result = prepare_prompt(
            prompt,
            role="verifier",
            model="verifier",
            config=HeadroomConfig(enabled=True),
            upstream_base_url="https://upstream.invalid/v1",
            timeout_seconds=5,
            urlopen=fake_urlopen,
        )

        self.assertIn("EXACT CRITERION", result.prompt)
        self.assertIn('"verdict": "pass | repair | blocked"', result.prompt)
        self.assertIn('"status": "met | missing | uncertain"', result.prompt)
        self.assertNotIn("supporting handoff", result.prompt)


    def test_proxy_transport_failure_falls_back_but_upstream_failure_does_not(self):
        config = HeadroomConfig(enabled=True, fail_open=True)
        direct = StubProvider("direct")
        transport_failure = StubProvider(
            failure=ProviderError("proxy offline", classification="transport_error")
        )
        provider = HeadroomProvider(direct, transport_failure, config, "https://upstream.invalid/v1")
        prepared = HeadroomPromptResult("effective", {"status": "compressed"})

        with mock.patch("automation.model_providers.prepare_prompt", return_value=prepared):
            result = provider.invoke("original", model="m", timeout_seconds=5)

        self.assertEqual(result.text, "direct")
        self.assertEqual(direct.prompts, ["original"])
        self.assertEqual(result.telemetry["compression"]["status"], "proxy_unavailable")

        direct = StubProvider("direct")
        upstream_failure = StubProvider(
            failure=ProviderError(
                "upstream auth",
                classification="authentication_failed",
                status_code=401,
            )
        )
        provider = HeadroomProvider(direct, upstream_failure, config, "https://upstream.invalid/v1")
        with mock.patch("automation.model_providers.prepare_prompt", return_value=prepared):
            with self.assertRaises(ProviderError) as raised:
                provider.invoke("original", model="m", timeout_seconds=5)

        self.assertEqual(raised.exception.classification, "authentication_failed")
        self.assertEqual(raised.exception.status_code, 401)
        self.assertEqual(direct.prompts, [])

    def test_routing_headers_and_free_only_settings_survive_headroom_wrapper(self):
        config = model_config_from_values(
            "implementer",
            {
                "transport": "openai-compatible-chat-completions",
                "model": "vendor/model:free",
                "fallback_models": ["vendor/backup:free"],
                "free_only": True,
                "base_url": "https://openrouter.invalid/api/v1",
                "headroom": {
                    "enabled": True,
                    "proxy_url": "http://127.0.0.1:8787/v1",
                },
            },
        )
        provider = create_provider(config)

        self.assertIsInstance(provider, HeadroomProvider)
        validate_safe_headers(proxy_headers(config.base_url))
        self.assertEqual(
            provider.proxy_provider.headers["X-Headroom-Base-Url"],
            config.base_url,
        )
        self.assertEqual(provider.proxy_provider.headers["X-Headroom-Bypass"], "true")
        body = provider.proxy_provider.build_body(config.model, "prompt")
        self.assertEqual(body["models"], ["vendor/model:free", "vendor/backup:free"])
        self.assertFalse(body["provider"]["allow_fallbacks"])

    def test_headroom_rejects_command_transport_and_redacts_url_secrets(self):
        with self.assertRaises(ProviderError):
            model_config_from_values(
                "implementer",
                {
                    "transport": "command",
                    "model": "local",
                    "command": "ollama run local",
                    "headroom": {"enabled": True},
                },
            )

        config = HeadroomConfig(
            enabled=True,
            proxy_url="http://user:password@localhost:8787/v1?token=secret",
        )
        serialized = json.dumps(config.safe_metadata())
        self.assertNotIn("password", serialized)
        self.assertNotIn("token=secret", serialized)



if __name__ == "__main__":
    unittest.main()
