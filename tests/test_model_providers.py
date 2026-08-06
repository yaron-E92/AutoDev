import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from automation.model_providers import (
    ChatCompletionsProvider,
    CommandProvider,
    ModelConfig,
    ProviderError,
    ResponsesProvider,
    build_chat_completions_body,
    build_responses_body,
    classify_http_status,
    model_config_from_values,
    normalize_provider_name,
    resolve_model_config,
    validate_safe_headers,
)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class ModelProviderTests(unittest.TestCase):
    def test_openai_compatible_alias_maps_to_chat_completions(self):
        self.assertEqual(
            normalize_provider_name("openai-compatible"),
            "openai-compatible-chat-completions",
        )
        self.assertEqual(
            normalize_provider_name("chat-completions"),
            "openai-compatible-chat-completions",
        )
        self.assertEqual(
            normalize_provider_name("responses"),
            "openai-compatible-responses",
        )

    def test_command_provider_passes_prompt_on_stdin(self):
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".py") as handle:
            handle.write("import sys\nprint('seen:' + sys.stdin.read())\n")
            script = handle.name
        try:
            provider = CommandProvider(f"{subprocess.list2cmdline([sys.executable, script])}")
            response = provider.generate("prompt", model="unused", timeout_seconds=10)
        finally:
            Path(script).unlink(missing_ok=True)

        self.assertIn("seen:prompt", response)

    def test_chat_completions_body_uses_user_message_and_omits_default_limit(self):
        body = build_chat_completions_body("model-a", "hello")

        self.assertEqual(body["model"], "model-a")
        self.assertEqual(body["messages"], [{"role": "user", "content": "hello"}])
        self.assertNotIn("max_tokens", body)

    def test_explicit_output_limits_are_transport_specific(self):
        chat = build_chat_completions_body("m", "p", output_limit=2048)
        responses = build_responses_body("m", "p", output_limit=4096)

        self.assertEqual(chat["max_tokens"], 2048)
        self.assertNotIn("max_output_tokens", chat)
        self.assertEqual(responses["max_output_tokens"], 4096)
        self.assertNotIn("max_tokens", responses)

    def test_responses_body_uses_input_and_explicit_options(self):
        body = build_responses_body(
            "model-r",
            "hello",
            request_options={"temperature": 0.1},
        )

        self.assertEqual(body["model"], "model-r")
        self.assertEqual(body["input"], "hello")
        self.assertEqual(body["temperature"], 0.1)

    def test_chat_completions_provider_works_without_api_key(self):
        with mock.patch("urllib.request.urlopen", return_value=FakeResponse({"choices": [{"message": {"content": "ok"}}]})) as urlopen:
            provider = ChatCompletionsProvider("http://localhost:1234/v1")
            response = provider.generate("prompt", model="m", timeout_seconds=5)

        self.assertEqual(response, "ok")
        sent = urlopen.call_args[0][0]
        self.assertNotIn("Authorization", sent.headers)

    def test_chat_completions_provider_reads_api_key_env(self):
        with mock.patch.dict(os.environ, {"TEST_API_KEY": "secret"}):
            with mock.patch("urllib.request.urlopen", return_value=FakeResponse({"choices": [{"message": {"content": "ok"}}]})) as urlopen:
                provider = ChatCompletionsProvider("http://localhost:1234/v1", "TEST_API_KEY")
                provider.generate("prompt", model="m", timeout_seconds=5)

        sent = urlopen.call_args[0][0]
        self.assertEqual(sent.headers["Authorization"], "Bearer secret")

    def test_responses_provider_extracts_output_text_and_usage(self):
        payload = {
            "model": "reported-model",
            "output": [
                {
                    "content": [
                        {"type": "output_text", "text": "first"},
                        {"type": "output_text", "text": " second"},
                    ]
                }
            ],
            "usage": {"input_tokens": 10, "output_tokens": 4},
        }
        with mock.patch("urllib.request.urlopen", return_value=FakeResponse(payload)):
            result = ResponsesProvider("http://localhost:1234/v1").invoke(
                "prompt",
                model="m",
                timeout_seconds=5,
            )

        self.assertEqual(result.text, "first second")
        self.assertEqual(result.telemetry["reported_model"], "reported-model")
        self.assertEqual(result.telemetry["usage"]["output_tokens"], 4)

    def test_http_statuses_have_actionable_classifications(self):
        self.assertEqual(classify_http_status(401), "authentication_failed")
        self.assertEqual(classify_http_status(402), "payment_required")
        self.assertEqual(classify_http_status(404), "not_found")
        self.assertEqual(classify_http_status(429), "rate_limited")

    def test_safe_headers_reject_secrets_and_metadata_omits_values(self):
        with self.assertRaises(ProviderError):
            validate_safe_headers({"Authorization": "Bearer secret"})

        config = model_config_from_values(
            "planner",
            {
                "transport": "openai-compatible-chat-completions",
                "model": "m",
                "base_url": "https://example.invalid/v1",
                "headers": {"X-Title": "not-secret"},
            },
        )
        metadata = json.dumps(config.safe_metadata())
        self.assertIn("X-Title", metadata)
        self.assertNotIn("not-secret", metadata)

    def test_free_only_disables_provider_fallbacks_and_rejects_paid_models(self):
        body = build_chat_completions_body(
            "vendor/model:free",
            "prompt",
            free_only=True,
            fallback_models=("vendor/backup:free",),
        )

        self.assertEqual(body["models"], ["vendor/model:free", "vendor/backup:free"])
        self.assertFalse(body["provider"]["allow_fallbacks"])
        with self.assertRaises(ProviderError):
            build_chat_completions_body(
                "vendor/model:free",
                "prompt",
                free_only=True,
                fallback_models=("vendor/paid",),
            )

    def test_reader_and_coder_configs_can_differ_and_cli_overrides_file(self):
        file_config = {
            "reader": {"provider": "chat-completions", "base_url": "http://reader/v1", "model": "reader-file"},
            "coder": {"provider": "command", "command": "coder --old", "model": "coder-file"},
        }
        reader = resolve_model_config(
            "reader",
            defaults={"provider": "command", "model": "default-reader"},
            file_config=file_config,
            cli_values={"model": "reader-cli"},
        )
        coder = resolve_model_config(
            "coder",
            defaults={"provider": "command", "model": "default-coder"},
            file_config=file_config,
            cli_values={"command": "coder --new"},
        )

        self.assertEqual(reader.provider, "openai-compatible-chat-completions")
        self.assertEqual(reader.model, "reader-cli")
        self.assertEqual(coder.provider, "command")
        self.assertEqual(coder.command, "coder --new")


if __name__ == "__main__":
    unittest.main()
