from __future__ import annotations

import json
import sys
from pathlib import Path

from area_reader_v2 import runner_core as _core
from area_reader_v2.runner_core import *  # noqa: F401,F403
from automation.model_providers import ModelConfig, create_provider, load_provider_config, ollama_command_for_model
from automation.model_roles import (
    ModelInvocationError,
    append_invocation_metadata,
    invoke_model,
    safe_role_metadata,
    resolve_role_configs,
)
from automation.prompt_policies import (
    compose_prompt,
    resolve_prompt_policies,
    role_policy_metadata,
    safe_prompt_policy_metadata,
)

_ORIGINAL_PARSE_ARGS = _core.parse_args
_ACTIVE_CONFIGS: dict[str, ModelConfig | None] = {}
_ACTIVE_POLICIES: dict[str, str] = resolve_prompt_policies({})
_ACTIVE_OUT: Path | None = None


def parse_args(argv=None):
    values = list(argv or sys.argv[1:])
    provider_config = ""
    if "--provider-config" in values:
        index = values.index("--provider-config")
        try:
            provider_config = values[index + 1]
        except IndexError as exc:
            raise SystemExit("--provider-config requires a path") from exc
        del values[index:index + 2]
    args = _ORIGINAL_PARSE_ARGS(values)
    args.provider_config = provider_config
    return args


def resolve_area_role_configs(args) -> dict[str, ModelConfig | None]:
    reader_model = args.reader_model or args.reader
    coder_model = args.coder_model or args.coder
    defaults = {
        "reader": _legacy_config(args, "reader", reader_model),
        "coder": _legacy_config(args, "coder", coder_model),
    }
    configs = resolve_role_configs(
        defaults={key: _config_dict(value) for key, value in defaults.items()},
        file_config=load_provider_config(args.provider_config),
    )
    if args.synthesizer and configs["synthesizer"] == configs["reader"]:
        reader = configs["reader"]
        assert reader is not None
        configs["synthesizer"] = ModelConfig(
            reader.provider,
            args.synthesizer,
            ollama_command_for_model(args.synthesizer) if reader.provider == "command" else reader.command,
            reader.base_url,
            reader.api_key_env,
            reader.timeout_seconds,
        )
    return configs


def resolve_area_prompt_policies(args) -> dict[str, str]:
    return resolve_prompt_policies(load_provider_config(args.provider_config))


def _legacy_config(args, role: str, model: str | None) -> ModelConfig:
    if not model:
        raise RuntimeError(f"{role} model is required")
    provider = getattr(args, f"{role}_provider")
    command = getattr(args, f"{role}_command")
    if provider == "command" and not command:
        command = ollama_command_for_model(str(model))
    return ModelConfig(
        provider,
        str(model),
        command,
        getattr(args, f"{role}_base_url"),
        getattr(args, f"{role}_api_key_env"),
        getattr(args, f"{role}_timeout_seconds"),
    )


def _config_dict(config: ModelConfig) -> dict[str, object]:
    return {
        "provider": config.provider,
        "model": config.model,
        "command": config.command,
        "base_url": config.base_url,
        "api_key_env": config.api_key_env,
        "timeout_seconds": config.timeout_seconds,
    }


def call_provider(args, role, prompt, num_predict, model_override=None):
    actual_role = "planner" if role == "coder" else "synthesizer" if model_override is not None else "reader"
    config = _ACTIVE_CONFIGS.get(actual_role)
    if config is None:
        raise RuntimeError(f"provider role is disabled: {actual_role}")
    provider = create_provider(config)
    effective_prompt = compose_prompt(actual_role, prompt, _ACTIVE_POLICIES[actual_role])
    policy_metadata = role_policy_metadata(actual_role, _ACTIVE_POLICIES)
    try:
        content, record = invoke_model(provider, config, effective_prompt, role=actual_role)
    except ModelInvocationError as exc:
        exc.record.update(policy_metadata)
        if _ACTIVE_OUT is not None:
            append_invocation_metadata(_ACTIVE_OUT / "model-invocations.json", exc.record)
        raise
    record.update(policy_metadata)
    if _ACTIVE_OUT is not None:
        append_invocation_metadata(_ACTIVE_OUT / "model-invocations.json", record)
    return {
        "message": {"content": content, "thinking": ""},
        "done_reason": "stop",
        "provider": config.provider,
        "model": config.model,
        "eval_count": 0,
        "prompt_eval_count": 0,
    }, float(record["elapsed_seconds"])


def main(argv=None):
    global _ACTIVE_CONFIGS, _ACTIVE_POLICIES, _ACTIVE_OUT
    args = parse_args(argv)
    _ACTIVE_CONFIGS = resolve_area_role_configs(args)
    _ACTIVE_POLICIES = resolve_area_prompt_policies(args)
    _ACTIVE_OUT = Path(args.out).expanduser().resolve()
    original_parse = _core.parse_args
    original_call = _core.call_provider
    try:
        _core.parse_args = parse_args
        _core.call_provider = call_provider
        code = _core.main(argv)
    finally:
        _core.parse_args = original_parse
        _core.call_provider = original_call
    if code == 0:
        summary_path = _ACTIVE_OUT / "summary.json"
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            summary = {}
        if isinstance(summary, dict):
            summary["roles"] = safe_role_metadata(_ACTIVE_CONFIGS)
            summary["prompt_policy"] = safe_prompt_policy_metadata(_ACTIVE_POLICIES)
            summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
