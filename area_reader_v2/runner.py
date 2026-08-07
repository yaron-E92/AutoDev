from __future__ import annotations

import json
import re
import sys
from dataclasses import replace
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
from automation.run_manifest import (
    ManifestError,
    complete_stage,
    hash_text,
    load_manifest,
    record_failure,
    stage_completed,
    stage_role_fingerprint,
    sync_invocations,
)

_ORIGINAL_PARSE_ARGS = _core.parse_args
_ACTIVE_CONFIGS: dict[str, ModelConfig | None] = {}
_ACTIVE_POLICIES: dict[str, str] = resolve_prompt_policies({})
_ACTIVE_OUT: Path | None = None
_ACTIVE_MANIFEST: Path | None = None


def parse_args(argv=None):
    values = list(argv or sys.argv[1:])
    provider_config = ""
    resume_manifest = ""
    for flag, destination in (("--provider-config", "provider"), ("--resume-manifest", "resume")):
        if flag not in values:
            continue
        index = values.index(flag)
        try:
            value = values[index + 1]
        except IndexError as exc:
            raise SystemExit(f"{flag} requires a path") from exc
        del values[index:index + 2]
        if destination == "provider":
            provider_config = value
        else:
            resume_manifest = value
    args = _ORIGINAL_PARSE_ARGS(values)
    args.provider_config = provider_config
    args.resume_manifest = resume_manifest
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
        configs["synthesizer"] = replace(
            reader,
            model=args.synthesizer,
            command=(
                ollama_command_for_model(args.synthesizer)
                if reader.provider == "command"
                else reader.command
            ),
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

    if actual_role == "synthesizer":
        _checkpoint_repository_read()
    elif actual_role == "planner":
        _checkpoint_handoff()

    replay = _replay_content(actual_role, prompt)
    if replay is not None:
        return {
            "message": {"content": replay, "thinking": ""},
            "done_reason": "stop",
            "provider": config.provider,
            "model": config.model,
            "eval_count": 0,
            "prompt_eval_count": 0,
            "resumed": True,
        }, 0.0

    provider = create_provider(config)
    effective_prompt = compose_prompt(actual_role, prompt, _ACTIVE_POLICIES[actual_role])
    policy_metadata = role_policy_metadata(actual_role, _ACTIVE_POLICIES)
    try:
        content, record = invoke_model(provider, config, effective_prompt, role=actual_role)
    except ModelInvocationError as exc:
        exc.record.update(policy_metadata)
        if _ACTIVE_OUT is not None:
            append_invocation_metadata(_ACTIVE_OUT / "model-invocations.json", exc.record)
            _sync_invocations()
        if _ACTIVE_MANIFEST is not None and _ACTIVE_MANIFEST.is_file():
            record_failure(
                _ACTIVE_MANIFEST,
                classification=str(exc.record.get("failure_classification", "provider_error")),
                reason=f"{actual_role} provider invocation failed",
                stage=_stage_for_role(actual_role),
            )
        raise
    record.update(policy_metadata)
    if _ACTIVE_OUT is not None:
        append_invocation_metadata(_ACTIVE_OUT / "model-invocations.json", record)
        _sync_invocations()
    return {
        "message": {"content": content, "thinking": ""},
        "done_reason": "stop",
        "provider": config.provider,
        "model": config.model,
        "eval_count": 0,
        "prompt_eval_count": 0,
    }, float(record["elapsed_seconds"])


def _replay_content(role: str, prompt: str) -> str | None:
    if _ACTIVE_MANIFEST is None or _ACTIVE_OUT is None or not _ACTIVE_MANIFEST.is_file():
        return None
    manifest = load_manifest(_ACTIVE_MANIFEST)
    required_stage = _stage_for_role(role)
    if not stage_completed(manifest, required_stage):
        return None
    if role == "reader":
        match = re.search(r"area reader model for area:\s*([^\.\n]+)", prompt, flags=re.IGNORECASE)
        if not match:
            raise ManifestError("cannot identify area-reader checkpoint from replay prompt")
        area = match.group(1).strip()
        path = _ACTIVE_OUT / f"area-{area}" / "reader-brief.md"
    elif role == "synthesizer":
        path = _ACTIVE_OUT / "synthesis-brief.md"
    else:
        path = _ACTIVE_OUT / "coder-plan.md"
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ManifestError(f"resume checkpoint artifact is missing: {path}") from exc


def _checkpoint_repository_read() -> None:
    if _ACTIVE_MANIFEST is None or _ACTIVE_OUT is None or not _ACTIVE_MANIFEST.is_file():
        return
    manifest = load_manifest(_ACTIVE_MANIFEST)
    if stage_completed(manifest, "repository-read"):
        return
    artifacts: list[Path] = []
    for name in ("repo-map.txt", "routing.json", "detected-facts.json", "recommended-command-groups.json", "verification-command-groups.json"):
        path = _ACTIVE_OUT / name
        if path.is_file():
            artifacts.append(path)
    artifacts.extend(sorted(_ACTIVE_OUT.glob("area-*/reader-brief.md")))
    complete_stage(
        _ACTIVE_MANIFEST,
        "repository-read",
        run_root=_ACTIVE_OUT.parent,
        artifacts=artifacts,
        inputs={
            "issue_sha256": _issue_hash(),
            "reader_fingerprint": stage_role_fingerprint(manifest, "reader"),
        },
        details={"area_count": len(list(_ACTIVE_OUT.glob("area-*/reader-brief.md")))},
    )
    _sync_invocations()


def _checkpoint_handoff() -> None:
    if _ACTIVE_MANIFEST is None or _ACTIVE_OUT is None or not _ACTIVE_MANIFEST.is_file():
        return
    _checkpoint_repository_read()
    manifest = load_manifest(_ACTIVE_MANIFEST)
    if stage_completed(manifest, "handoff-synthesized"):
        return
    complete_stage(
        _ACTIVE_MANIFEST,
        "handoff-synthesized",
        run_root=_ACTIVE_OUT.parent,
        artifacts=[_ACTIVE_OUT / "synthesis-brief.md", _ACTIVE_OUT / "synthesis-prompt.txt"],
        inputs={
            "repository_read_output": _stage_output_hash(manifest, "repository-read"),
            "synthesizer_fingerprint": stage_role_fingerprint(manifest, "synthesizer"),
        },
    )
    _sync_invocations()


def _checkpoint_plan() -> None:
    if _ACTIVE_MANIFEST is None or _ACTIVE_OUT is None or not _ACTIVE_MANIFEST.is_file():
        return
    _checkpoint_handoff()
    manifest = load_manifest(_ACTIVE_MANIFEST)
    if stage_completed(manifest, "plan-created"):
        return
    complete_stage(
        _ACTIVE_MANIFEST,
        "plan-created",
        run_root=_ACTIVE_OUT.parent,
        artifacts=[_ACTIVE_OUT / "coder-plan.md", _ACTIVE_OUT / "coder-prompt.txt"],
        inputs={
            "handoff_output": _stage_output_hash(manifest, "handoff-synthesized"),
            "planner_fingerprint": stage_role_fingerprint(manifest, "planner"),
        },
    )
    _sync_invocations()


def _stage_output_hash(manifest: dict[str, object], stage: str) -> str:
    stages = manifest.get("stages", {})
    record = stages.get(stage, {}) if isinstance(stages, dict) else {}
    return str(record.get("output_hash", "")) if isinstance(record, dict) else ""


def _issue_hash() -> str:
    if _ACTIVE_OUT is None:
        return ""
    try:
        return hash_text((_ACTIVE_OUT / "issue.txt").read_text(encoding="utf-8"))
    except OSError:
        return ""


def _sync_invocations() -> None:
    if _ACTIVE_MANIFEST is None or _ACTIVE_OUT is None or not _ACTIVE_MANIFEST.is_file():
        return
    sync_invocations(_ACTIVE_MANIFEST, _ACTIVE_OUT / "model-invocations.json")


def _stage_for_role(role: str) -> str:
    return {
        "reader": "repository-read",
        "synthesizer": "handoff-synthesized",
        "planner": "plan-created",
    }[role]


def main(argv=None):
    global _ACTIVE_CONFIGS, _ACTIVE_POLICIES, _ACTIVE_OUT, _ACTIVE_MANIFEST
    args = parse_args(argv)
    _ACTIVE_CONFIGS = resolve_area_role_configs(args)
    _ACTIVE_POLICIES = resolve_area_prompt_policies(args)
    _ACTIVE_OUT = Path(args.out).expanduser().resolve()
    _ACTIVE_MANIFEST = Path(args.resume_manifest).expanduser().resolve() if args.resume_manifest else None
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
        _checkpoint_plan()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
