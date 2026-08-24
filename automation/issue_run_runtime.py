from __future__ import annotations

import json
import os
import re
import shutil
import sys
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, TextIO
from automation import run_real_issue_core as _core
from automation.run_real_issue_core import *  # noqa: F401,F403
from automation.model_providers import ModelConfig, ModelProvider, ProviderResponse, create_provider, load_provider_config
from automation.model_roles import (
    MODEL_ROLES,
    ModelInvocationError,
    append_invocation_metadata,
    invoke_model,
    model_config_to_dict,
    resolve_role_configs,
    safe_role_metadata,
)
from automation.prompt_policies import (
    compose_prompt,
    resolve_prompt_policies,
    role_policy_metadata,
    safe_prompt_policy_metadata,
)
from automation.run_manifest import (
    MANIFEST_NAME,
    ManifestError,
    build_role_snapshot,
    complete_stage,
    create_manifest,
    hash_file,
    hash_text,
    load_manifest,
    manifest_path,
    next_stage,
    reconcile_role_snapshots,
    record_failure,
    record_stage_state,
    render_status,
    save_manifest,
    stage_completed,
    stage_role_fingerprint,
    sync_invocations,
    update_pr,
    validate_artifacts,
)
from automation.semantic_verifier import (
    SemanticSettings,
    SemanticVerifierError,
    build_schema_repair_prompt,
    build_semantic_prompt,
    build_semantic_repair_prompt,
    collect_changed_files,
    collect_current_diff,
    parse_semantic_output,
    resolve_semantic_settings,
    safe_semantic_metadata,
    write_final_verdict,
    write_semantic_result,
)
from automation.issue_run_models import (
    write_compression_debug_artifact,
)
from automation.issue_run_resume import (
    _build_role_snapshots,
)
from automation.issue_run_session import (
    _ACTIVE_MANIFEST,
    _ACTIVE_RESUMING,
    _ACTIVE_ROLES,
    _ACTIVE_ROLE_SNAPSHOTS,
    _CORE_WRITE_OPERATIONAL_OUTPUTS,
    _active_manifest_path,
    _file_hash_or_empty,
    _policies_or_default,
    _roles_or_legacy,
    _semantic_settings_or_disabled,
    _stage_output_hash,
    _sync_manifest_invocations,
)

def resolve_role_provider_configs(args) -> dict[str, ModelConfig | None]:
    file_config = load_provider_config(args.provider_config)
    defaults = {
        "reader": _core.default_ollama_command_config(_core.DEFAULT_READER_MODEL),
        "coder": _core.default_ollama_command_config(_core.DEFAULT_CODER_MODEL),
    }
    cli_values = {
        role: _core.provider_cli_values(args, role, file_config, defaults[role])
        for role in ("reader", "coder")
    }
    return resolve_role_configs(defaults=defaults, file_config=file_config, cli_values=cli_values)

def resolve_prompt_policy_configs(args) -> dict[str, str]:
    return resolve_prompt_policies(load_provider_config(args.provider_config))

def resolve_semantic_verification_settings(
    args,
    roles: dict[str, ModelConfig | None],
) -> SemanticSettings:
    return resolve_semantic_settings(
        load_provider_config(args.provider_config),
        verifier_configured=roles.get("verifier") is not None,
    )

def resolve_provider_configs(args):
    roles = _ACTIVE_ROLES.get() or resolve_role_provider_configs(args)
    reader = roles["reader"]
    implementer = roles["implementer"]
    assert reader is not None and implementer is not None
    return reader, implementer

def run_area_reader(repo, issue_text, reader_config, coder_config, out_dir, stream):
    roles = _roles_or_legacy(reader_config, coder_config)
    policies = _policies_or_default()
    if out_dir.exists() and not _ACTIVE_RESUMING.get():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    config_path = out_dir / "resolved-provider-roles.json"
    config_path.write_text(
        json.dumps(
            {
                "version": 2,
                "roles": {
                    role: model_config_to_dict(config)
                    for role, config in roles.items()
                    if config is not None
                },
                "prompt_policy": {
                    "enabled": any(mode != "off" for mode in policies.values()),
                    "roles": policies,
                },
                "semantic_verification": safe_semantic_metadata(_semantic_settings_or_disabled()),
            },
            indent=2,
            sort_keys=True,
        ) + "\n",
        encoding="utf-8",
    )
    argv = [
        "--repo", str(repo),
        "--reader-model", reader_config.model,
        "--coder-model", coder_config.model,
        "--provider-config", str(config_path),
        "--issue", issue_text,
        "--out", str(out_dir),
        "--resume-manifest", str(_active_manifest_path()),
    ]
    print("Running shared area-reader planner", file=stream)
    try:
        exit_code = area_reader_runner.main(argv)  # noqa: F405
    except (ManifestError, ModelInvocationError) as exc:
        raise RunnerError(str(exc), 1) from exc  # noqa: F405
    if exit_code:
        raise RunnerError(f"area-reader planner failed with exit code {exit_code}", exit_code)  # noqa: F405

def write_operational_outputs(issue_text, area_out, out_dir, keep_debug):
    source = area_out / "model-invocations.json"
    if source.is_file():
        shutil.copyfile(source, out_dir / "model-invocations.json")
        if keep_debug:
            try:
                records = json.loads(source.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                records = []
            if isinstance(records, list):
                for record in records:
                    if isinstance(record, dict):
                        write_compression_debug_artifact(out_dir, record)
    if _ACTIVE_MANIFEST.get() is None:
        _CORE_WRITE_OPERATIONAL_OUTPUTS(issue_text, area_out, out_dir, keep_debug)
        return
    if _ACTIVE_RESUMING.get():
        problems = validate_artifacts(load_manifest(_active_manifest_path()), out_dir)
        if problems:
            raise RunnerError("resume artifact validation failed after area-reader replay: " + "; ".join(problems), 2)  # noqa: F405
    _CORE_WRITE_OPERATIONAL_OUTPUTS(issue_text, area_out, out_dir, True)
    _refresh_operational_checkpoints(area_out, out_dir)
    _sync_manifest_invocations(out_dir)

def _refresh_operational_checkpoints(area_out: Path, out_dir: Path) -> None:
    manifest_file = _active_manifest_path()
    manifest = load_manifest(manifest_file)
    if stage_completed(manifest, "repository-read"):
        reader_artifacts = [
            out_dir / "routed-areas.json",
            out_dir / "detected-facts.json",
            out_dir / "recommended-command-groups.json",
            out_dir / "verification-command-groups.json",
            *sorted(area_out.glob("area-*/reader-brief.md")),
        ]
        complete_stage(
            manifest_file,
            "repository-read",
            run_root=out_dir,
            artifacts=reader_artifacts,
            inputs={
                "issue_sha256": _file_hash_or_empty(out_dir / "issue.md"),
                "reader_fingerprint": stage_role_fingerprint(manifest, "reader"),
            },
            details={"area_count": len(list(area_out.glob("area-*/reader-brief.md")))},
        )
        manifest = load_manifest(manifest_file)
    if stage_completed(manifest, "handoff-synthesized"):
        complete_stage(
            manifest_file,
            "handoff-synthesized",
            run_root=out_dir,
            artifacts=[out_dir / "synthesized-handoff.md", area_out / "synthesis-prompt.txt", area_out / "synthesis-brief.md"],
            inputs={
                "repository_read_output": _stage_output_hash(manifest, "repository-read"),
                "synthesizer_fingerprint": stage_role_fingerprint(manifest, "synthesizer"),
            },
        )
        manifest = load_manifest(manifest_file)
    if stage_completed(manifest, "plan-created"):
        complete_stage(
            manifest_file,
            "plan-created",
            run_root=out_dir,
            artifacts=[out_dir / "coder-plan.md", area_out / "coder-prompt.txt", area_out / "coder-plan.md"],
            inputs={
                "handoff_output": _stage_output_hash(manifest, "handoff-synthesized"),
                "planner_fingerprint": stage_role_fingerprint(manifest, "planner"),
            },
        )

def write_provider_metadata(out_dir, reader_config, coder_config):
    roles = _roles_or_legacy(reader_config, coder_config)
    snapshots = _ACTIVE_ROLE_SNAPSHOTS.get() or _build_role_snapshots(roles, _policies_or_default())
    write_json(  # noqa: F405
        out_dir / "provider-metadata.json",
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "reader": reader_config.safe_metadata(),
            "coder": coder_config.safe_metadata(),
            "roles": safe_role_metadata(roles),
            "prompt_policy": safe_prompt_policy_metadata(_policies_or_default()),
            "semantic_verification": safe_semantic_metadata(_semantic_settings_or_disabled()),
            "resume_role_snapshots": snapshots,
        },
    )
