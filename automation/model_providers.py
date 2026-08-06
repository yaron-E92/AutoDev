from __future__ import annotations

import json
import os
import shlex
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib import error, request


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


class CommandProvider(ModelProvider):
    def __init__(self, command: str):
        self.command = command

    def invoke(self, prompt: str, *, model: str, timeout_seconds: int) -> ProviderResponse:
        if not self.command:
            raise ProviderError("command provider requires a command", classification="invalid_config")
        try:
            argv = shlex.split(self.command, posix=os.name != "nt")
        except ValueError as exc:
            raise ProviderError("command provider command is malformed", classification="invalid_config") from exc
        if not argv:
            raise ProviderError("command provider command is empty", classification="invalid_config")

        prompt_path: Path | None = None
        try:
            if "{prompt}" in self.command or "{prompt_file}" in self.command:
                with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
                    handle.write(prompt)
                    prompt_path = Path(handle.name)
                rendered = self.command.replace(
                    "{prompt_file}", quote_shell_argument(str(prompt_path))
                ).replace("{prompt}", quote_shell_argument(prompt))
                completed = subprocess.run(
                    rendered,
                    shell=True,
                    text=True,
                    capture_output=True,
                    timeout=timeout_seconds,
                    check=False,
                )
            else:
                completed = subprocess.run(
                    argv,
                    input=prompt,
                    text=True,
                    capture_output=True,
                    timeout=timeout_seconds,
                    check=False,
                )
        except subprocess.TimeoutExpired as exc:
            raise ProviderError("command provider timed out", classification="timeout") from exc
        except OSError as exc:
            raise ProviderError(
                f"command executable is unavailable: {argv[0]}",
                classification="command_unavailable",
            ) from exc
        finally:
            if prompt_path is not None:
                prompt_path.unlink(missing_ok=True)

        if completed.returncode != 0:
            raise ProviderError(
                f"command provider exited with {completed.returncode}: {argv[0]}",
                classification="command_failed",
            )
        return ProviderResponse(completed.stdout, {"returncode": completed.returncode})


class _OpenAICompatibleProvider(ModelProvider):
    endpoint = ""

    def __init__(
        self,
        base_url: str,
        api_key_env: str = "",
        *,
        headers: dict[str, str] | None = None,
        request_options: dict[str, object] | None = None,
        output_limit: int | None = None,
        free_only: bool = False,
        fallback_models: tuple[str, ...] = (),
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key_env = api_key_env
        self.headers = validate_safe_headers(headers or {})
        self.request_options = dict(request_options or {})
        self.output_limit = output_limit
        self.free_only = free_only
        self.fallback_models = fallback_models

    def invoke(self, prompt: str, *, model: str, timeout_seconds: int) -> ProviderResponse:
        if not self.base_url:
            raise ProviderError(
                f"{self.endpoint} provider requires a base URL",
                classification="invalid_config",
            )
        api_key = ""
        if self.api_key_env:
            api_key = os.environ.get(self.api_key_env, "")
            if not api_key:
                raise ProviderError(
                    f"environment variable is not set: {self.api_key_env}",
                    classification="missing_credentials",
                )

        body = self.build_body(model, prompt)
        headers = {"Content-Type": "application/json", **self.headers}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        req = request.Request(
            f"{self.base_url}/{self.endpoint}",
            data=json.dumps(body).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            retry_after = exc.headers.get("Retry-After", "") if exc.headers else ""
            raise ProviderError(
                http_failure_message(exc.code),
                classification=classify_http_status(exc.code),
                status_code=exc.code,
                retry_after=retry_after,
            ) from exc
        except (TimeoutError, subprocess.TimeoutExpired) as exc:
            raise ProviderError("provider request timed out", classification="timeout") from exc
        except error.URLError as exc:
            classification = "timeout" if isinstance(exc.reason, TimeoutError) else "transport_error"
            raise ProviderError("provider endpoint is unreachable", classification=classification) from exc
        except json.JSONDecodeError as exc:
            raise ProviderError("provider returned malformed JSON", classification="malformed_response") from exc

        if not isinstance(payload, dict):
            raise ProviderError("provider returned a malformed response", classification="malformed_response")
        return ProviderResponse(self.extract_text(payload), response_telemetry(payload))

    def build_body(self, model: str, prompt: str) -> dict[str, Any]:
        raise NotImplementedError

    def extract_text(self, payload: dict[str, object]) -> str:
        raise NotImplementedError


class ChatCompletionsProvider(_OpenAICompatibleProvider):
    endpoint = "chat/completions"

    def build_body(self, model: str, prompt: str) -> dict[str, Any]:
        return build_chat_completions_body(
            model,
            prompt,
            request_options=self.request_options,
            output_limit=self.output_limit,
            free_only=self.free_only,
            fallback_models=self.fallback_models,
        )

    def extract_text(self, payload: dict[str, object]) -> str:
        try:
            content = payload["choices"][0]["message"]["content"]  # type: ignore[index]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(
                "chat-completions response did not include assistant content",
                classification="malformed_response",
            ) from exc
        if not isinstance(content, str):
            raise ProviderError(
                "chat-completions assistant content was not text",
                classification="malformed_response",
            )
        return content


class ResponsesProvider(_OpenAICompatibleProvider):
    endpoint = "responses"

    def build_body(self, model: str, prompt: str) -> dict[str, Any]:
        return build_responses_body(
            model,
            prompt,
            request_options=self.request_options,
            output_limit=self.output_limit,
            free_only=self.free_only,
            fallback_models=self.fallback_models,
        )

    def extract_text(self, payload: dict[str, object]) -> str:
        output_text = payload.get("output_text")
        if isinstance(output_text, str):
            return output_text
        output = payload.get("output")
        if isinstance(output, list):
            parts: list[str] = []
            for item in output:
                if not isinstance(item, dict):
                    continue
                content = item.get("content")
                if not isinstance(content, list):
                    continue
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "output_text" and isinstance(part.get("text"), str):
                        parts.append(str(part["text"]))
            if parts:
                return "".join(parts)
        raise ProviderError(
            "responses response did not include output text",
            classification="malformed_response",
        )


class MockProvider(ModelProvider):
    def __init__(self, responses: list[str] | None = None):
        self.responses = list(responses or ["NO_CHANGES_REQUIRED\nmock response"])
        self.prompts: list[str] = []

    def invoke(self, prompt: str, *, model: str, timeout_seconds: int) -> ProviderResponse:
        self.prompts.append(prompt)
        if self.responses:
            return ProviderResponse(self.responses.pop(0), {})
        return ProviderResponse("NO_CHANGES_REQUIRED\nmock response", {})


def quote_shell_argument(value: str) -> str:
    if os.name == "nt":
        return subprocess.list2cmdline([value])
    return shlex.quote(value)


def build_chat_completions_body(
    model: str,
    prompt: str,
    *,
    request_options: dict[str, object] | None = None,
    output_limit: int | None = None,
    free_only: bool = False,
    fallback_models: tuple[str, ...] = (),
) -> dict[str, Any]:
    body = validated_request_options(request_options, reserved={"model", "models", "messages", "max_tokens"})
    apply_model_selection(body, model, fallback_models, free_only)
    body["messages"] = [{"role": "user", "content": prompt}]
    if output_limit is not None:
        body["max_tokens"] = validate_output_limit(output_limit)
    apply_free_only_routing(body, free_only)
    return body


def build_responses_body(
    model: str,
    prompt: str,
    *,
    request_options: dict[str, object] | None = None,
    output_limit: int | None = None,
    free_only: bool = False,
    fallback_models: tuple[str, ...] = (),
) -> dict[str, Any]:
    body = validated_request_options(request_options, reserved={"model", "models", "input", "max_output_tokens"})
    apply_model_selection(body, model, fallback_models, free_only)
    body["input"] = prompt
    if output_limit is not None:
        body["max_output_tokens"] = validate_output_limit(output_limit)
    apply_free_only_routing(body, free_only)
    return body


def validated_request_options(
    values: dict[str, object] | None,
    *,
    reserved: set[str],
) -> dict[str, Any]:
    options = dict(values or {})
    conflicts = sorted(reserved.intersection(options))
    if conflicts:
        raise ProviderError(
            "request_options cannot override: " + ", ".join(conflicts),
            classification="invalid_config",
        )
    return options


def apply_model_selection(
    body: dict[str, Any],
    model: str,
    fallback_models: tuple[str, ...],
    free_only: bool,
) -> None:
    models = (model, *fallback_models)
    if free_only and any(not item.endswith(":free") for item in models):
        raise ProviderError(
            "free-only configuration requires every model and fallback to end with :free",
            classification="free_only_violation",
        )
    if fallback_models:
        body["models"] = list(models)
    else:
        body["model"] = model


def apply_free_only_routing(body: dict[str, Any], free_only: bool) -> None:
    if not free_only:
        return
    provider = body.get("provider", {})
    if not isinstance(provider, dict):
        raise ProviderError("request_options.provider must be an object", classification="invalid_config")
    provider = dict(provider)
    provider["allow_fallbacks"] = False
    body["provider"] = provider


def validate_output_limit(value: int) -> int:
    if value <= 0:
        raise ProviderError("output_limit must be greater than zero", classification="invalid_config")
    return value


def validate_safe_headers(values: dict[str, str]) -> dict[str, str]:
    safe: dict[str, str] = {}
    for name, value in values.items():
        normalized = str(name).strip().casefold()
        if normalized in SENSITIVE_HEADER_NAMES or normalized not in SAFE_HEADER_NAMES:
            raise ProviderError(f"header is not allowlisted: {name}", classification="invalid_config")
        safe[str(name)] = str(value)
    return safe


def classify_http_status(status_code: int) -> str:
    return {
        401: "authentication_failed",
        402: "payment_required",
        404: "not_found",
        408: "timeout",
        429: "rate_limited",
    }.get(status_code, "http_error")


def http_failure_message(status_code: int) -> str:
    return {
        401: "provider authentication failed (HTTP 401)",
        402: "provider payment or plan is required (HTTP 402)",
        404: "provider endpoint or model was not found (HTTP 404)",
        408: "provider request timed out (HTTP 408)",
        429: "provider rate limit or quota was exhausted (HTTP 429)",
    }.get(status_code, f"provider request failed (HTTP {status_code})")


def response_telemetry(payload: dict[str, object]) -> dict[str, object]:
    telemetry: dict[str, object] = {}
    reported_model = payload.get("model")
    if isinstance(reported_model, str):
        telemetry["reported_model"] = reported_model
    usage = payload.get("usage")
    if isinstance(usage, dict):
        safe_usage = {
            str(key): value
            for key, value in usage.items()
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        }
        if safe_usage:
            telemetry["usage"] = safe_usage
        cost = usage.get("cost")
        if isinstance(cost, (int, float)) and not isinstance(cost, bool):
            telemetry["reported_cost"] = cost
    cost = payload.get("cost")
    if isinstance(cost, (int, float)) and not isinstance(cost, bool):
        telemetry["reported_cost"] = cost
    return telemetry


def normalize_provider_name(value: str) -> str:
    normalized = PROVIDER_ALIASES.get(value.strip().casefold(), value.strip().casefold())
    if normalized not in SUPPORTED_PROVIDERS:
        raise ProviderError(f"unsupported provider transport: {value}", classification="invalid_config")
    return normalized


def ollama_command_for_model(model: str) -> str:
    return f"ollama run {shlex.quote(model)}"


def create_provider(config: ModelConfig, mock_responses: list[str] | None = None) -> ModelProvider:
    provider = normalize_provider_name(config.provider)
    if provider == "command":
        return CommandProvider(config.command)
    common = {
        "headers": config.headers,
        "request_options": config.request_options,
        "output_limit": config.output_limit,
        "free_only": config.free_only,
        "fallback_models": config.fallback_models,
    }
    if provider == "openai-compatible-chat-completions":
        return ChatCompletionsProvider(config.base_url, config.api_key_env, **common)
    if provider == "openai-compatible-responses":
        return ResponsesProvider(config.base_url, config.api_key_env, **common)
    if provider == "mock":
        return MockProvider(mock_responses)
    raise ProviderError(f"unsupported provider transport: {config.provider}", classification="invalid_config")


def load_provider_config(path: str | None) -> dict[str, object]:
    if not path:
        return {}
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ProviderError("provider config must be a JSON object", classification="invalid_config")
    return data


def model_config_from_values(
    role: str,
    values: dict[str, object],
) -> ModelConfig:
    provider_value = values.get("transport", values.get("provider", "command"))
    provider = normalize_provider_name(str(provider_value))
    model = str(values.get("model", "")).strip()
    command = str(values.get("command", "")).strip()
    base_url = str(values.get("base_url", "")).strip()
    api_key_env = str(values.get("api_key_env", "")).strip()
    timeout_seconds = int(values.get("timeout_seconds", 600))
    headers = object_string_map(values.get("headers", {}), f"{role} headers")
    request_options = object_map(values.get("request_options", {}), f"{role} request_options")
    output_limit_value = values.get("output_limit")
    output_limit = None if output_limit_value in (None, "") else int(output_limit_value)
    profile_name = str(values.get("profile_name", values.get("profile", ""))).strip()
    free_only = bool(values.get("free_only", False))
    fallback_value = values.get("fallback_models", [])
    if fallback_value is None:
        fallback_value = []
    if not isinstance(fallback_value, list):
        raise ProviderError(f"{role} fallback_models must be an array", classification="invalid_config")
    fallback_models = tuple(str(item).strip() for item in fallback_value if str(item).strip())
    direct_edit = bool(values.get("direct_edit", False))

    if timeout_seconds <= 0:
        raise ProviderError(f"{role} timeout must be greater than zero", classification="invalid_config")
    if output_limit is not None:
        validate_output_limit(output_limit)
    if not model and provider != "command":
        raise ProviderError(f"{role} provider requires a model", classification="invalid_config")
    if provider == "command" and not command:
        raise ProviderError(f"{role} command provider requires a command", classification="invalid_config")
    if provider.startswith("openai-compatible-") and not base_url:
        raise ProviderError(f"{role} HTTP provider requires a base URL", classification="invalid_config")
    validate_safe_headers(headers)
    if free_only:
        apply_model_selection({}, model, fallback_models, True)

    return ModelConfig(
        provider=provider,
        model=model,
        command=command,
        base_url=base_url,
        api_key_env=api_key_env,
        timeout_seconds=timeout_seconds,
        headers=headers,
        request_options=request_options,
        output_limit=output_limit,
        profile_name=profile_name,
        free_only=free_only,
        fallback_models=fallback_models,
        direct_edit=direct_edit,
    )


def object_map(value: object, label: str) -> dict[str, object]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ProviderError(f"{label} must be an object", classification="invalid_config")
    return {str(key): item for key, item in value.items()}


def object_string_map(value: object, label: str) -> dict[str, str]:
    return {key: str(item) for key, item in object_map(value, label).items()}


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
            raise ProviderError(f"provider config section must be an object: {role}", classification="invalid_config")
        merged.update(role_config)
    for key, value in cli_values.items():
        if value not in (None, ""):
            merged[key] = value
    return model_config_from_values(role, merged)
