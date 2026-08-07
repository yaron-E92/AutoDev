from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from automation.headroom import HeadroomError, resolve_headroom_values
from automation.model_providers import (
    ModelConfig,
    ModelProvider,
    ProviderError,
    ProviderResponse,
    model_config_from_values,
    normalize_provider_name,
    ollama_command_for_model,
)

MODEL_ROLES = ("reader", "synthesizer", "planner", "implementer", "fixer", "verifier")
ROLE_FALLBACKS = {
    "reader": "reader",
    "synthesizer": "reader",
    "planner": "coder",
    "implementer": "coder",
    "fixer": "coder",
    "verifier": "coder",
}


class ModelInvocationError(ProviderError):
    def __init__(self, record: dict[str, object]):
        classification = str(record.get("failure_classification", "provider_error"))
        super().__init__(
            f"{record['role']} provider call failed ({classification})",
            classification=classification,
            status_code=record.get("status_code") if isinstance(record.get("status_code"), int) else None,
        )
        self.record = record


def resolve_role_configs(
    *,
    defaults: dict[str, dict[str, object]],
    file_config: dict[str, object],
    cli_values: dict[str, dict[str, object]] | None = None,
) -> dict[str, ModelConfig | None]:
    version = file_config.get("version")
    if version not in (None, 2):
        raise ProviderError(f"unsupported provider config version: {version}", classification="invalid_config")

    if version == 2:
        explicit_roles = file_config.get("roles")
        if not isinstance(explicit_roles, dict):
            raise ProviderError("provider config version 2 requires a roles object", classification="invalid_config")
        unknown = sorted(set(explicit_roles) - set(MODEL_ROLES))
        if unknown:
            raise ProviderError("unknown provider role(s): " + ", ".join(unknown), classification="invalid_config")
    else:
        explicit_roles = {
            role: file_config[role]
            for role in MODEL_ROLES
            if role not in {"reader", "planner", "implementer", "fixer"} and role in file_config
        }

    profile_name = str(file_config.get("name", file_config.get("profile_name", ""))).strip()
    cli_values = cli_values or {}
    resolved: dict[str, ModelConfig | None] = {}
    for role in MODEL_ROLES:
        explicit = explicit_roles.get(role)
        if explicit is not None and not isinstance(explicit, dict):
            raise ProviderError(f"provider config section must be an object: {role}", classification="invalid_config")
        if role == "verifier" and explicit is None:
            resolved[role] = None
            continue

        fallback = ROLE_FALLBACKS[role]
        if fallback not in defaults:
            raise ProviderError(f"missing default provider configuration: {fallback}", classification="invalid_config")
        merged = dict(defaults[fallback])
        overrides: dict[str, object] = {}

        legacy = file_config.get(fallback, {})
        if legacy:
            if not isinstance(legacy, dict):
                raise ProviderError(f"provider config section must be an object: {fallback}", classification="invalid_config")
            merged.update(legacy)
            overrides.update(legacy)

        if explicit is not None:
            merged.update(explicit)
            overrides.update(explicit)
        else:
            for key, value in cli_values.get(fallback, {}).items():
                if value not in (None, ""):
                    merged[key] = value
                    overrides[key] = value

        try:
            headroom_values = resolve_headroom_values(file_config, role)
        except HeadroomError as exc:
            raise ProviderError(str(exc), classification="invalid_config") from exc
        provider_value = merged.get("transport", merged.get("provider", "command"))
        if headroom_values and normalize_provider_name(str(provider_value)).startswith("openai-compatible-"):
            merged["headroom"] = headroom_values

        if profile_name and not merged.get("profile_name") and not merged.get("profile"):
            merged["profile_name"] = profile_name
        resolved[role] = _model_config(role, merged, overrides)
    return resolved


def _model_config(role: str, values: dict[str, object], overrides: dict[str, object]) -> ModelConfig:
    provider_value = values.get("transport", values.get("provider", "command"))
    provider = normalize_provider_name(str(provider_value))
    model = str(values.get("model", "")).strip()
    command = str(values.get("command", "")).strip()

    if provider == "command":
        requested_provider = str(overrides.get("transport", overrides.get("provider", ""))).strip().casefold()
        if requested_provider == "ollama":
            command = command or ollama_command_for_model(model)
        elif overrides.get("model") not in (None, "") and overrides.get("command") in (None, ""):
            command = ollama_command_for_model(model)
        if not command and model:
            command = ollama_command_for_model(model)
        values = dict(values)
        values["provider"] = provider
        values["command"] = command
        values["base_url"] = ""
        values["api_key_env"] = ""
        values["headers"] = {}
        values["request_options"] = {}
        values["output_limit"] = None
        values["free_only"] = False
        values["fallback_models"] = []
    else:
        values = dict(values)
        values["provider"] = provider
        values["command"] = ""

    return model_config_from_values(role, values)


def model_config_to_dict(config: ModelConfig) -> dict[str, object]:
    return {
        "transport": config.provider,
        "provider": config.provider,
        "model": config.model,
        "command": config.command,
        "base_url": config.base_url,
        "api_key_env": config.api_key_env,
        "timeout_seconds": config.timeout_seconds,
        "headers": dict(config.headers),
        "request_options": dict(config.request_options),
        "output_limit": config.output_limit,
        "profile_name": config.profile_name,
        "free_only": config.free_only,
        "fallback_models": list(config.fallback_models),
        "direct_edit": config.direct_edit,
        "headroom": config.headroom.safe_metadata(),
    }


def safe_role_metadata(configs: dict[str, ModelConfig | None]) -> dict[str, object]:
    return {
        role: config.safe_metadata() if config is not None else {"enabled": False}
        for role, config in configs.items()
    }


def invoke_model(
    provider: ModelProvider,
    config: ModelConfig,
    prompt: str,
    *,
    role: str,
    attempt: int = 0,
) -> tuple[str, dict[str, object]]:
    if role not in MODEL_ROLES:
        raise ProviderError(f"unknown model role: {role}", classification="invalid_config")
    started_at = datetime.now(timezone.utc)
    started = time.monotonic()
    record: dict[str, object] = {
        "role": role,
        "attempt": attempt,
        "retry_count": attempt,
        **config.safe_metadata(),
        "started_at": started_at.isoformat(),
    }
    try:
        if "generate" in type(provider).__dict__ and "invoke" not in type(provider).__dict__:
            response = ProviderResponse(
                provider.generate(prompt, model=config.model, timeout_seconds=config.timeout_seconds),
                {},
            )
        else:
            response = provider.invoke(prompt, model=config.model, timeout_seconds=config.timeout_seconds)
    except Exception as exc:
        classification = exc.classification if isinstance(exc, ProviderError) else type(exc).__name__
        record.update(
            {
                "ended_at": datetime.now(timezone.utc).isoformat(),
                "elapsed_seconds": round(time.monotonic() - started, 6),
                "status": "failure",
                "error_type": type(exc).__name__,
                "failure_classification": classification,
            }
        )
        if isinstance(exc, ProviderError):
            if exc.status_code is not None:
                record["status_code"] = exc.status_code
            if exc.retry_after:
                record["retry_after"] = exc.retry_after
        raise ModelInvocationError(record) from exc
    record.update(
        {
            "ended_at": datetime.now(timezone.utc).isoformat(),
            "elapsed_seconds": round(time.monotonic() - started, 6),
            "status": "success",
        }
    )
    record.update(response.telemetry)
    return response.text, record


def append_invocation_metadata(path: Path, record: dict[str, object]) -> None:
    records: list[object] = []
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(value, list):
            records = value
    except (OSError, json.JSONDecodeError):
        pass
    records.append(record)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(records, indent=2, sort_keys=True) + "\n", encoding="utf-8")
