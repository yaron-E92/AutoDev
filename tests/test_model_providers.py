import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from automation.model_providers import (
    ChatCompletionsProvider,
    CommandProvider,
    ModelConfig,
    build_chat_completions_body,
    normalize_provider_name,
    resolve_model_config,
)


class ModelProviderTests(unittest.TestCase):
    def test_openai_compatible_alias_maps_to_chat_completions(self):
        self.assertEqual(normalize_provider_name("openai-compatible"), "chat-completions")

    def test_command_provider_passes_prompt_on_stdin(self):
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".py") as handle:
            handle.write("import sys\nprint('seen:' + sys.stdin.read())\n")
            script = handle.name
        try:
            provider = CommandProvider(f"{subprocess.list2cmdline(['python3', script])}")
            response = provider.generate("prompt", model="unused", timeout_seconds=10)
        finally:
            Path(script).unlink(missing_ok=True)

        self.assertIn("seen:prompt", response)

    def test_chat_completions_body_uses_user_message(self):
        body = build_chat_completions_body("model-a", "hello")

        self.assertEqual(body["model"], "model-a")
        self.assertEqual(body["messages"], [{"role": "user", "content": "hello"}])

    def test_chat_completions_provider_works_without_api_key(self):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode("utf-8")

        with mock.patch("urllib.request.urlopen", return_value=FakeResponse()) as urlopen:
            provider = ChatCompletionsProvider("http://localhost:1234/v1")
            response = provider.generate("prompt", model="m", timeout_seconds=5)

        self.assertEqual(response, "ok")
        request = urlopen.call_args[0][0]
        self.assertNotIn("Authorization", request.headers)

    def test_chat_completions_provider_reads_api_key_env(self):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode("utf-8")

        with mock.patch.dict(os.environ, {"TEST_API_KEY": "secret"}):
            with mock.patch("urllib.request.urlopen", return_value=FakeResponse()) as urlopen:
                provider = ChatCompletionsProvider("http://localhost:1234/v1", "TEST_API_KEY")
                provider.generate("prompt", model="m", timeout_seconds=5)

        request = urlopen.call_args[0][0]
        self.assertEqual(request.headers["Authorization"], "Bearer secret")

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

        self.assertEqual(reader.provider, "chat-completions")
        self.assertEqual(reader.model, "reader-cli")
        self.assertEqual(coder.provider, "command")
        self.assertEqual(coder.command, "coder --new")


if __name__ == "__main__":
    unittest.main()
