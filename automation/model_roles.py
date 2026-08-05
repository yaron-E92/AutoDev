from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from automation.model_providers import ModelConfig, ModelProvider, ProviderError, normalize_provider_name, ollama_command_for_model

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
        super().__init__(f"{record['role']} provider call failed ({record['error_type']})")
        self.record = record


def resolve_role_configs(
    *,
    defaults: dict[str, dict[str, object]],
    file_config: dict[str, object],
    cli_values: dict[str, dict[str, object]] | None = None,
) -> dict[str, ModelConfig | None]:
    version = file_config.get("version")
    if version not in (None, 2):
        raise ProviderError(f"unsupported provider config version: {version}")

    if version == 2:
        explicit_roles = file_config.get("roles")
        if not isinstance(explicit_roles, dict):
            raise ProviderError("provider config version 2 requires a roles object")
        unknown = sorted(set(explicit_roles) - set(MODEL_ROLES))
        if unknown:
            raise ProviderError("unknown provider role(s): " + ", ".join(unknown))
    else:
        explicit_roles = {
            role: file_config[role]
            for role in MODEL_ROLES
            if role not in {"reader", "planner", "implementer", "fixer"} and role in file_config
        }

    cli_values = cli_values or {}
    resolved: dict[str, ModelConfig | None] = {}
    for role in MODEL_ROLES:
        explicit = explicit_roles.get(role)
        if explicit is not None and not isinstance(explicit, dict):
            raise ProviderError(f"provider config section must be an object: {role}")
        if role == "verifier" and explicit is None:
            resolved[role] = None
            continue

        fallback = ROLE_FALLBACKS[role]
        if fallback not in defaults:
            raise ProviderError(f"missing default provider configuration: {fallback}")
        merged = dict(defaults[fallback])
        overrides: dict[str, object] = {}

        legacy = file_config.get(fallback, {})
        if legacy:
            if not isinstance(legacy, dict):
                raise ProviderError(f"provider config section must be an object: {fallback}")
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

        resolved[role] = _model_config(role, merged, overrides)
    return resolved


def _model_config(role: str, values: dict[str, object], overrides: dict[str, object]) -> ModelConfig:
    provider = normalize_provider_name(str(values.get("provider", "command")))
    model = str(values.get("model", "")).strip()
    command = str(values.get("command", "")).strip()
    base_url = str(values.get("base_url", "")).strip()
    api_key_env = str(values.get("api_key_env", "")).strip()
    timeout_seconds = int(values.get("timeout_seconds", 600))

    if provider == "command":
        if overrides.get("model") not in (None, "") and overrides.get("command") in (None, ""):
            command = ollama_command_for_model(model)
        if not command and model:
            command = ollama_command_for_model(model)
        base_url = ""
        api_key_env = ""
    else:
        command = ""
    if provider != "chat-completions":
        base_url = ""
        api_key_env = ""

    if timeout_seconds <= 0:
        raise ProviderError(f"{role} timeout must be greater than zero")
    if not model and provider != "command":
        raise ProviderError(f"{role} provider requires a model")
    if provider == "command" and not command:
        raise ProviderError(f"{role} command provider requires a command")
    if provider == "chat-completions" and not base_url:
        raise ProviderError(f"{role} chat-completions provider requires a base URL")
    return ModelConfig(provider, model, command, base_url, api_key_env, timeout_seconds)


def model_config_to_dict(config: ModelConfig) -> dict[str, object]:
    return {
        "provider": config.provider,
        "model": config.model,
        "command": config.command,
        "base_url": config.base_url,
        "api_key_env": config.api_key_env,
        "timeout_seconds": config.timeout_seconds,
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
        raise ProviderError(f"unknown model role: {role}")
    started_at = datetime.now(timezone.utc)
    started = time.monotonic()
    record: dict[str, object] = {
        "role": role,
        "attempt": attempt,
        **config.safe_metadata(),
        "started_at": started_at.isoformat(),
    }
    try:
        response = provider.generate(prompt, model=config.model, timeout_seconds=config.timeout_seconds)
    except Exception as exc:
        record.update(
            {
                "ended_at": datetime.now(timezone.utc).isoformat(),
                "elapsed_seconds": round(time.monotonic() - started, 6),
                "status": "failure",
                "error_type": type(exc).__name__,
            }
        )
        raise ModelInvocationError(record) from exc
    record.update(
        {
            "ended_at": datetime.now(timezone.utc).isoformat(),
            "elapsed_seconds": round(time.monotonic() - started, 6),
            "status": "success",
        }
    )
    return response, record


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
