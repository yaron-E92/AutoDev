from __future__ import annotations

import os
import shlex
from dataclasses import dataclass, field
from automation.headroom import (
    HeadroomConfig,
    HeadroomError,
    headroom_config_from_values,
    prepare_prompt,
    proxy_headers,
)


PROVIDER_ALIASES = {
    "openai-compatible": "openai-compatible-chat-completions",
    "chat-completions": "openai-compatible-chat-completions",
    "responses": "openai-compatible-responses",
    "ollama": "command",
}

SUPPORTED_PROVIDERS = {
    "command",
    "openai-compatible-chat-completions",
    "openai-compatible-responses",
    "mock",
}

SAFE_HEADER_NAMES = {
    "accept",
    "groq-beta",
    "http-referer",
    "user-agent",
    "x-headroom-base-url",
    "x-headroom-bypass",
    "x-openrouter-metadata",
    "x-title",
}

SENSITIVE_HEADER_NAMES = {
    "authorization",
    "cookie",
    "proxy-authorization",
    "set-cookie",
    "x-api-key",
}

class ProviderError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        classification: str = "provider_error",
        status_code: int | None = None,
        retry_after: str = "",
    ):
        super().__init__(message)
        self.classification = classification
        self.status_code = status_code
        self.retry_after = retry_after

@dataclass(frozen=True)
class ProviderResponse:
    text: str
    telemetry: dict[str, object] = field(default_factory=dict)

@dataclass(frozen=True)
class ModelConfig:
    provider: str
    model: str
    command: str = ""
    base_url: str = ""
    api_key_env: str = ""
    timeout_seconds: int = 600
    headers: dict[str, str] = field(default_factory=dict)
    request_options: dict[str, object] = field(default_factory=dict)
    output_limit: int | None = None
    profile_name: str = ""
    free_only: bool = False
    fallback_models: tuple[str, ...] = ()
    direct_edit: bool = False
    headroom: HeadroomConfig = field(default_factory=HeadroomConfig)

    @property
    def transport(self) -> str:
        return self.provider

    def safe_metadata(self) -> dict[str, object]:
        metadata: dict[str, object] = {
            "provider": self.provider,
            "transport": self.provider,
            "model": self.model,
            "timeout_seconds": self.timeout_seconds,
            "free_only": self.free_only,
            "direct_edit": self.direct_edit,
            "headroom": self.headroom.safe_metadata(),
        }
        if self.profile_name:
            metadata["profile_name"] = self.profile_name
        if self.command:
            try:
                metadata["command"] = shlex.split(self.command, posix=os.name != "nt")[0]
            except ValueError:
                metadata["command"] = "configured"
        if self.base_url:
            metadata["base_url"] = self.base_url
        if self.api_key_env:
            metadata["api_key_env"] = self.api_key_env
            metadata["api_key_configured"] = bool(os.environ.get(self.api_key_env))
        if self.headers:
            metadata["header_names"] = sorted(self.headers)
        if self.request_options:
            metadata["request_option_names"] = sorted(self.request_options)
        if self.output_limit is not None:
            metadata["output_limit"] = self.output_limit
        if self.fallback_models:
            metadata["fallback_models"] = list(self.fallback_models)
        return metadata

class ModelProvider:
    def invoke(self, prompt: str, *, model: str, timeout_seconds: int) -> ProviderResponse:
        raise NotImplementedError

    def generate(self, prompt: str, *, model: str, timeout_seconds: int) -> str:
        return self.invoke(prompt, model=model, timeout_seconds=timeout_seconds).text
