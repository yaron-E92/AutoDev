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
from automation.issue_run_session import (
    _ACTIVE_DEBUG_ARTIFACTS,
    _ACTIVE_MANIFEST,
    _active_manifest_path,
    _policies_or_default,
    _sync_manifest_invocations,
)

def call_coder(provider, config, prompt, out_dir, attempt, *, role="implementer", response_name=None):
    metadata_path = out_dir / "model-invocations.json"
    policies = _policies_or_default()
    prompt = compose_prompt(role, prompt, policies[role])
    policy_metadata = role_policy_metadata(role, policies)
    try:
        response, record = invoke_model(provider, config, prompt, role=role, attempt=attempt)
    except ModelInvocationError as exc:
        exc.record.update(policy_metadata)
        append_invocation_metadata(metadata_path, exc.record)
        if _ACTIVE_MANIFEST.get() is not None:
            _sync_manifest_invocations(out_dir)
            record_failure(
                _active_manifest_path(),
                classification=str(exc.record.get("failure_classification", "provider_error")),
                reason=f"{role} provider invocation failed",
                stage=_stage_for_model_role(role),
            )
        raise
    record.update(policy_metadata)
    append_invocation_metadata(metadata_path, record)
    if _ACTIVE_MANIFEST.get() is not None:
        _sync_manifest_invocations(out_dir)
    if _ACTIVE_DEBUG_ARTIFACTS.get():
        write_compression_debug_artifact(out_dir, record)
    name = response_name or f"attempt-{attempt}.txt"
    write_text(out_dir / "model-responses" / name, response)  # noqa: F405
    return response

def _stage_for_model_role(role: str) -> str:
    return {
        "reader": "repository-read",
        "synthesizer": "handoff-synthesized",
        "planner": "plan-created",
        "implementer": "implementation-generated",
        "fixer": "repair-generated",
        "verifier": "semantic-verified",
    }.get(role, "")

def write_compression_debug_artifact(out_dir: Path, record: dict[str, object]) -> None:
    compression = record.get("compression")
    if not isinstance(compression, dict):
        return
    role = str(record.get("role", "unknown"))
    attempt = record.get("attempt", 0)
    payload = {
        "role": role,
        "attempt": attempt,
        "transport": record.get("transport", record.get("provider", "")),
        "model": record.get("model", ""),
        **compression,
    }
    write_json(out_dir / "compression" / f"{role}-attempt-{attempt}.json", payload)  # noqa: F405
