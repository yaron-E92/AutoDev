from __future__ import annotations

import json
import shlex
from automation.headroom import (
    HeadroomConfig,
    HeadroomError,
    headroom_config_from_values,
    prepare_prompt,
    proxy_headers,
)

from automation.provider_command import (
    CommandProvider,
)
from automation.provider_contract import (
    ModelConfig,
    ModelProvider,
    PROVIDER_ALIASES,
    ProviderError,
    SUPPORTED_PROVIDERS,
)
from automation.provider_headroom import (
    HeadroomProvider,
)
from automation.provider_http import (
    ChatCompletionsProvider,
    ResponsesProvider,
    _OpenAICompatibleProvider,
)
from automation.provider_mock import (
    MockProvider,
)
from automation.provider_requests import (
    apply_model_selection,
    validate_output_limit,
    validate_safe_headers,
)

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
    if provider == "mock":
        return MockProvider(mock_responses)

    common = {
        "headers": config.headers,
        "request_options": config.request_options,
        "output_limit": config.output_limit,
        "free_only": config.free_only,
        "fallback_models": config.fallback_models,
    }
    provider_type: type[_OpenAICompatibleProvider]
    if provider == "openai-compatible-chat-completions":
        provider_type = ChatCompletionsProvider
    elif provider == "openai-compatible-responses":
        provider_type = ResponsesProvider
    else:
        raise ProviderError(f"unsupported provider transport: {config.provider}", classification="invalid_config")

    direct = provider_type(config.base_url, config.api_key_env, **common)
    if not config.headroom.enabled:
        return direct

    proxy_common = dict(common)
    proxy_common["headers"] = {**config.headers, **proxy_headers(config.base_url)}
    proxied = provider_type(config.headroom.proxy_url, config.api_key_env, **proxy_common)
    return HeadroomProvider(direct, proxied, config.headroom, config.base_url)

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
    try:
        headroom = headroom_config_from_values(values.get("headroom", {}))
    except HeadroomError as exc:
        raise ProviderError(str(exc), classification="invalid_config") from exc

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
    if headroom.enabled and not provider.startswith("openai-compatible-"):
        raise ProviderError(
            f"{role} Headroom compression requires an OpenAI-compatible HTTP transport",
            classification="invalid_config",
        )
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
        headroom=headroom,
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
