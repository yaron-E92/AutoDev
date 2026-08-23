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
from automation.issue_run_implementation import (
    run_implementation_loop,
)
from automation.issue_run_pull_request import (
    build_pr_body,
    create_draft_pr,
)
from automation.issue_run_resume import (
    _build_role_snapshots,
    _extract_resume_options,
    _inject_resume_arguments,
    _reconcile_semantic_settings,
    _update_resume_target_options,
    _validate_next_stage_provider,
)
from automation.issue_run_runtime import (
    resolve_prompt_policy_configs,
    resolve_provider_configs,
    resolve_role_provider_configs,
    resolve_semantic_verification_settings,
    run_area_reader,
    write_operational_outputs,
    write_provider_metadata,
)
from automation.issue_run_session import (
    _ACTIVE_ARGS,
    _ACTIVE_DEBUG_ARTIFACTS,
    _ACTIVE_FACTORY,
    _ACTIVE_MANIFEST,
    _ACTIVE_POLICIES,
    _ACTIVE_RESUMING,
    _ACTIVE_ROLES,
    _ACTIVE_ROLE_SNAPSHOTS,
    _ACTIVE_SEMANTIC,
    _DeferredProvider,
    _sync_manifest_invocations,
)

def run(argv=None, *, stdout=None, stderr=None, provider_factory=None):
    out_stream = stdout if stdout is not None else sys.stdout
    err_stream = stderr if stderr is not None else sys.stderr
    raw_values = list(argv if argv is not None else sys.argv[1:])
    try:
        values, resume_dir, status_only, invalidated_roles = _extract_resume_options(raw_values)
        resume_manifest = manifest_path(resume_dir) if resume_dir is not None else None
        if status_only:
            if resume_manifest is None:
                raise ManifestError("--status requires --resume <run-directory>")
            manifest = load_manifest(resume_manifest)
            problems = validate_artifacts(manifest, resume_dir)
            print(
                render_status(
                    manifest,
                    requested_invalidations=sorted(invalidated_roles),
                    artifact_problems=problems,
                ),
                end="",
                file=out_stream,
            )
            return 0 if not problems else 2
        if resume_manifest is not None:
            manifest = load_manifest(resume_manifest)
            values = _inject_resume_arguments(values, resume_dir, manifest)
        args = _core.parse_args(values)
        if resume_manifest is not None and args.allow_dirty:
            raise ManifestError("--allow-dirty is not supported while resuming; restore the recorded worktree instead")
    except (ManifestError, RunnerError, SystemExit) as exc:
        message = str(exc)
        if message:
            print(message, file=err_stream)
        return 2

    try:
        roles = resolve_role_provider_configs(args)
        policies = resolve_prompt_policy_configs(args)
        semantic = resolve_semantic_verification_settings(args, roles)
        role_snapshots = _build_role_snapshots(roles, policies)
        if resume_manifest is not None:
            reconcile_role_snapshots(
                resume_manifest,
                role_snapshots,
                explicit_invalidations=invalidated_roles,
            )
            _reconcile_semantic_settings(resume_manifest, semantic, invalidated_roles)
            _update_resume_target_options(resume_manifest, args)
            _validate_next_stage_provider(load_manifest(resume_manifest), roles)
    except (ManifestError, ProviderError, RunnerError, OSError, json.JSONDecodeError) as exc:
        print(str(exc), file=err_stream)
        return exc.exit_code if isinstance(exc, RunnerError) else 2

    actual_factory = provider_factory or create_provider
    role_token = _ACTIVE_ROLES.set(roles)
    factory_token = _ACTIVE_FACTORY.set(actual_factory)
    policy_token = _ACTIVE_POLICIES.set(policies)
    semantic_token = _ACTIVE_SEMANTIC.set(semantic)
    debug_token = _ACTIVE_DEBUG_ARTIFACTS.set(bool(args.debug_artifacts))
    args_token = _ACTIVE_ARGS.set(args)
    manifest_token = _ACTIVE_MANIFEST.set(resume_manifest or manifest_path(Path(args.out).expanduser().resolve()))
    resume_token = _ACTIVE_RESUMING.set(resume_manifest is not None)
    snapshots_token = _ACTIVE_ROLE_SNAPSHOTS.set(role_snapshots)
    originals = {
        "resolve_provider_configs": _core.resolve_provider_configs,
        "run_area_reader": _core.run_area_reader,
        "write_operational_outputs": _core.write_operational_outputs,
        "run_implementation_loop": _core.run_implementation_loop,
        "write_provider_metadata": _core.write_provider_metadata,
        "create_draft_pr": _core.create_draft_pr,
        "build_pr_body": _core.build_pr_body,
    }
    for name in ("require_tools", "select_issue", "fetch_issue_text", "ensure_clean_worktree", "ensure_issue_branch"):
        originals[name] = getattr(_core, name)
        setattr(_core, name, globals()[name])
    try:
        _core.resolve_provider_configs = resolve_provider_configs
        _core.run_area_reader = run_area_reader
        _core.write_operational_outputs = write_operational_outputs
        _core.run_implementation_loop = run_implementation_loop
        _core.write_provider_metadata = write_provider_metadata
        _core.create_draft_pr = create_draft_pr
        _core.build_pr_body = build_pr_body
        result = _core.run(
            values,
            stdout=stdout,
            stderr=stderr,
            provider_factory=lambda config: _DeferredProvider(config, actual_factory),
        )
        manifest_file = _ACTIVE_MANIFEST.get()
        if manifest_file is not None and manifest_file.is_file():
            _sync_manifest_invocations(manifest_file.parent)
            if result != 0:
                manifest = load_manifest(manifest_file)
                if not manifest.get("failure"):
                    record_failure(
                        manifest_file,
                        classification="runner_failed",
                        reason=f"runner exited with code {result}",
                    )
        return result
    finally:
        for name, value in originals.items():
            setattr(_core, name, value)
        _ACTIVE_ROLES.reset(role_token)
        _ACTIVE_FACTORY.reset(factory_token)
        _ACTIVE_POLICIES.reset(policy_token)
        _ACTIVE_SEMANTIC.reset(semantic_token)
        _ACTIVE_DEBUG_ARTIFACTS.reset(debug_token)
        _ACTIVE_ARGS.reset(args_token)
        _ACTIVE_MANIFEST.reset(manifest_token)
        _ACTIVE_RESUMING.reset(resume_token)
        _ACTIVE_ROLE_SNAPSHOTS.reset(snapshots_token)

def main(argv=None):
    return run(argv)
