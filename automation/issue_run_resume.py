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

def _extract_resume_options(values: list[str]) -> tuple[list[str], Path | None, bool, set[str]]:
    cleaned: list[str] = []
    resume_dir: Path | None = None
    status = False
    invalidated_roles: set[str] = set()
    index = 0
    while index < len(values):
        value = values[index]
        if value == "--status":
            status = True
            index += 1
            continue
        if value == "--resume":
            if index + 1 >= len(values):
                raise ManifestError("--resume requires a run directory")
            resume_dir = Path(values[index + 1]).expanduser().resolve()
            index += 2
            continue
        if value.startswith("--resume="):
            resume_dir = Path(value.split("=", 1)[1]).expanduser().resolve()
            index += 1
            continue
        if value == "--invalidate-role":
            if index + 1 >= len(values):
                raise ManifestError("--invalidate-role requires a role")
            invalidated_roles.add(values[index + 1].strip().casefold())
            index += 2
            continue
        if value.startswith("--invalidate-role="):
            invalidated_roles.add(value.split("=", 1)[1].strip().casefold())
            index += 1
            continue
        cleaned.append(value)
        index += 1
    unknown = sorted(invalidated_roles - set(MODEL_ROLES))
    if unknown:
        raise ManifestError("unknown --invalidate-role value(s): " + ", ".join(unknown))
    if (status or invalidated_roles) and resume_dir is None:
        raise ManifestError("--status and --invalidate-role require --resume <run-directory>")
    return cleaned, resume_dir, status, invalidated_roles

def _inject_resume_arguments(values: list[str], resume_dir: Path, manifest: dict[str, object]) -> list[str]:
    target = manifest.get("target", {})
    if not isinstance(target, dict):
        raise ManifestError("run manifest target is invalid")
    result = list(values)
    if "--next" in result:
        raise ManifestError("--next is not supported with --resume; the manifest already identifies the issue")
    required = {
        "--repo": str(target["repo_path"]),
        "--github-repo": str(target["github_repo"]),
        "--issue": str(target["issue_number"]),
        "--out": str(resume_dir),
        "--mode": str(target["mode"]),
    }
    for flag, expected in required.items():
        supplied = _argument_value(result, flag)
        supplied_value = (
            str(Path(supplied).expanduser().resolve())
            if supplied is not None and flag in {"--repo", "--out"}
            else supplied
        )
        expected_value = str(Path(expected).expanduser().resolve()) if flag in {"--repo", "--out"} else expected
        if supplied is not None and supplied_value != expected_value:
            raise ManifestError(f"resume {flag} does not match the manifest")
        if supplied is None:
            result.extend([flag, expected])

    provider_config = target.get("provider_config_path")
    if provider_config and _argument_value(result, "--provider-config") is None:
        result.extend(["--provider-config", str(provider_config)])

    options = target.get("options", {})
    if isinstance(options, dict):
        saved_fix_attempts = options.get("max_fix_attempts")
        supplied_fix_attempts = _argument_value(result, "--max-fix-attempts")
        if supplied_fix_attempts is not None and saved_fix_attempts is not None:
            if int(supplied_fix_attempts) != int(saved_fix_attempts):
                raise ManifestError(
                    "resume --max-fix-attempts differs from the recorded run; restart the run instead of changing repair policy"
                )
        elif supplied_fix_attempts is None and saved_fix_attempts is not None:
            result.extend(["--max-fix-attempts", str(saved_fix_attempts)])

        execution_flags = {
            "skip_implementation": "--skip-implementation",
            "dry_run_implementation": "--dry-run-implementation",
            "baseline_verify": "--baseline-verify",
            "managed_labels": "--manage-labels",
        }
        for key, flag in execution_flags.items():
            recorded = bool(options.get(key))
            supplied = flag in result
            if supplied and not recorded:
                raise ManifestError(f"resume {flag} differs from the recorded run configuration")
            if recorded and not supplied:
                result.append(flag)
        if options.get("debug_artifacts") and "--debug-artifacts" not in result:
            result.append("--debug-artifacts")
    return result

def _argument_value(values: list[str], flag: str) -> str | None:
    if flag in values:
        index = values.index(flag)
        return values[index + 1] if index + 1 < len(values) else ""
    prefix = flag + "="
    for value in values:
        if value.startswith(prefix):
            return value[len(prefix):]
    return None

def _build_role_snapshots(
    roles: dict[str, ModelConfig | None],
    policies: dict[str, str],
) -> dict[str, object]:
    snapshots: dict[str, object] = {}
    for role in MODEL_ROLES:
        config = roles.get(role)
        policy = role_policy_metadata(role, policies)
        if config is None:
            snapshots[role] = build_role_snapshot({}, {"enabled": False}, prompt_policy=policy)
            continue
        snapshots[role] = build_role_snapshot(
            model_config_to_dict(config),
            config.safe_metadata(),
            prompt_policy=policy,
        )
    return snapshots

def _reconcile_semantic_settings(
    path: Path,
    settings: SemanticSettings,
    invalidated_roles: set[str],
) -> None:
    manifest = load_manifest(path)
    current = safe_semantic_metadata(settings)
    previous = manifest.get("semantic_verification", {})
    if not isinstance(previous, dict):
        previous = {}
    if previous and previous != current:
        affected = [
            stage
            for stage in ("semantic-verified", "pr-created")
            if stage_completed(manifest, stage)
        ]
        if affected and "verifier" not in invalidated_roles:
            raise ManifestError(
                "semantic-verification configuration changed for completed work; "
                "resume requires --invalidate-role verifier"
            )
    manifest = load_manifest(path)
    manifest["semantic_verification"] = current
    save_manifest(path, manifest)

def _update_resume_target_options(path: Path, args) -> None:
    manifest = load_manifest(path)
    target = manifest.get("target", {})
    if not isinstance(target, dict):
        raise ManifestError("run manifest target is invalid")
    target["provider_config_path"] = _provider_config_path(args.provider_config)
    previous_options = target.get("options", {})
    debug_artifacts = bool(args.debug_artifacts)
    if isinstance(previous_options, dict):
        debug_artifacts = debug_artifacts or bool(previous_options.get("debug_artifacts"))
    target["options"] = {
        "max_fix_attempts": args.max_fix_attempts,
        "debug_artifacts": debug_artifacts,
        "skip_implementation": bool(args.skip_implementation),
        "dry_run_implementation": bool(args.dry_run_implementation),
        "baseline_verify": bool(args.baseline_verify),
        "managed_labels": bool(args.manage_labels or args.next),
    }
    save_manifest(path, manifest)

def _provider_config_path(value: str | None) -> str:
    if not value:
        return ""
    return str(Path(value).expanduser().resolve())

def _validate_next_stage_provider(manifest: dict[str, object], roles: dict[str, ModelConfig | None]) -> None:
    stage = next_stage(manifest)
    if stage == "semantic-verified":
        semantic = manifest.get("semantic_verification", {})
        if isinstance(semantic, dict) and semantic.get("enabled") is False:
            return
    role_for_stage = {
        "repository-read": "reader",
        "handoff-synthesized": "synthesizer",
        "plan-created": "planner",
        "implementation-generated": "implementer",
        "semantic-verified": "verifier",
    }
    role = role_for_stage.get(stage)
    if role is None:
        return
    config = roles.get(role)
    if config is None:
        raise RunnerError(f"resume requires a configured {role} provider for the next stage", 2)
    if config.api_key_env and not os.environ.get(config.api_key_env):
        raise RunnerError(
            f"resume requires environment variable {config.api_key_env} for the next {role} stage",
            2,
        )
