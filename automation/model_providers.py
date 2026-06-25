from __future__ import annotations

import json
import os
import shlex
import subprocess
from dataclasses import dataclass
from typing import Any
from urllib import error, request


PROVIDER_ALIASES = {
    "openai-compatible": "chat-completions",
}
SUPPORTED_PROVIDERS = {"command", "chat-completions", "mock"}


class ProviderError(RuntimeError):
    pass


@dataclass(frozen=True)
class ModelConfig:
    provider: str
    model: str
    command: str = ""
    base_url: str = ""
    api_key_env: str = ""
    timeout_seconds: int = 600

    def safe_metadata(self) -> dict[str, object]:
        metadata: dict[str, object] = {
            "provider": self.provider,
            "model": self.model,
            "timeout_seconds": self.timeout_seconds,
        }
        if self.command:
            metadata["command"] = shlex.split(self.command)[0]
        if self.base_url:
            metadata["base_url"] = self.base_url
        if self.api_key_env:
            metadata["api_key_env"] = self.api_key_env
            metadata["api_key_configured"] = bool(os.environ.get(self.api_key_env))
        return metadata


class ModelProvider:
    def generate(self, prompt: str, *, model: str, timeout_seconds: int) -> str:
        raise NotImplementedError


class CommandProvider(ModelProvider):
    def __init__(self, command: str):
        self.command = command

    def generate(self, prompt: str, *, model: str, timeout_seconds: int) -> str:
        if not self.command:
            raise ProviderError("command provider requires a command")
        argv = shlex.split(self.command)
        if not argv:
            raise ProviderError("command provider command is empty")
        completed = subprocess.run(
            argv,
            input=prompt,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
        if completed.returncode != 0:
            stderr = completed.stderr.strip()
            raise ProviderError(
                f"command provider exited with {completed.returncode}: {stderr or argv[0]}"
            )
        return completed.stdout


class ChatCompletionsProvider(ModelProvider):
    def __init__(self, base_url: str, api_key_env: str = ""):
        self.base_url = base_url.rstrip("/")
        self.api_key_env = api_key_env

    def generate(self, prompt: str, *, model: str, timeout_seconds: int) -> str:
        if not self.base_url:
            raise ProviderError("chat-completions provider requires a base URL")
        api_key = ""
        if self.api_key_env:
            api_key = os.environ.get(self.api_key_env, "")
            if not api_key:
                raise ProviderError(f"environment variable is not set: {self.api_key_env}")

        body = build_chat_completions_body(model, prompt)
        data = json.dumps(body).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        req = request.Request(
            f"{self.base_url}/chat/completions",
            data=data,
            headers=headers,
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            message = exc.read().decode("utf-8", errors="replace")
            raise ProviderError(f"chat-completions HTTP {exc.code}: {message}") from exc
        except (error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise ProviderError(f"chat-completions request failed: {exc}") from exc

        try:
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError("chat-completions response did not include assistant content") from exc
        if not isinstance(content, str):
            raise ProviderError("chat-completions assistant content was not text")
        return content


class MockProvider(ModelProvider):
    def __init__(self, responses: list[str] | None = None):
        self.responses = list(responses or ["NO_CHANGES_REQUIRED\nmock response"])
        self.prompts: list[str] = []

    def generate(self, prompt: str, *, model: str, timeout_seconds: int) -> str:
        self.prompts.append(prompt)
        if self.responses:
            return self.responses.pop(0)
        return "NO_CHANGES_REQUIRED\nmock response"


def build_chat_completions_body(model: str, prompt: str) -> dict[str, Any]:
    return {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
    }


def normalize_provider_name(value: str) -> str:
    normalized = PROVIDER_ALIASES.get(value, value)
    if normalized not in SUPPORTED_PROVIDERS:
        raise ProviderError(f"unsupported provider: {value}")
    return normalized


def create_provider(config: ModelConfig, mock_responses: list[str] | None = None) -> ModelProvider:
    provider = normalize_provider_name(config.provider)
    if provider == "command":
        return CommandProvider(config.command)
    if provider == "chat-completions":
        return ChatCompletionsProvider(config.base_url, config.api_key_env)
    if provider == "mock":
        return MockProvider(mock_responses)
    raise ProviderError(f"unsupported provider: {config.provider}")


def load_provider_config(path: str | None) -> dict[str, object]:
    if not path:
        return {}
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ProviderError("provider config must be a JSON object")
    return data


def resolve_model_config(
    role: str,
    *,
    defaults: dict[str, object],
    file_config: dict[str, object],
    cli_values: dict[str, object],
) -> ModelConfig:
    merged = dict(defaults)
    role_config = file_config.get(role, {})
    if role_config:
        if not isinstance(role_config, dict):
            raise ProviderError(f"provider config section must be an object: {role}")
        merged.update(role_config)
    for key, value in cli_values.items():
        if value not in (None, ""):
            merged[key] = value

    provider = normalize_provider_name(str(merged.get("provider", "command")))
    model = str(merged.get("model", "")).strip()
    command = str(merged.get("command", "")).strip()
    base_url = str(merged.get("base_url", "")).strip()
    api_key_env = str(merged.get("api_key_env", "")).strip()
    timeout_seconds = int(merged.get("timeout_seconds", 600))
    if timeout_seconds <= 0:
        raise ProviderError(f"{role} timeout must be greater than zero")
    if not model and provider != "command":
        raise ProviderError(f"{role} provider requires a model")
    if provider == "command" and not command:
        raise ProviderError(f"{role} command provider requires a command")
    if provider == "chat-completions" and not base_url:
        raise ProviderError(f"{role} chat-completions provider requires a base URL")
    if api_key_env and not os.environ.get(api_key_env):
        raise ProviderError(f"environment variable is not set: {api_key_env}")
    return ModelConfig(
        provider=provider,
        model=model,
        command=command,
        base_url=base_url,
        api_key_env=api_key_env,
        timeout_seconds=timeout_seconds,
    )
