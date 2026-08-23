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

_ACTIVE_ROLES: ContextVar[dict[str, ModelConfig | None] | None] = ContextVar("active_roles", default=None)

_ACTIVE_FACTORY: ContextVar[Callable[[ModelConfig], ModelProvider] | None] = ContextVar("active_factory", default=None)

_ACTIVE_POLICIES: ContextVar[dict[str, str] | None] = ContextVar("active_policies", default=None)

_ACTIVE_SEMANTIC: ContextVar[SemanticSettings | None] = ContextVar("active_semantic", default=None)

_ACTIVE_DEBUG_ARTIFACTS: ContextVar[bool] = ContextVar("active_debug_artifacts", default=False)

_ACTIVE_ARGS: ContextVar[object | None] = ContextVar("active_args", default=None)

_ACTIVE_MANIFEST: ContextVar[Path | None] = ContextVar("active_manifest", default=None)

_ACTIVE_RESUMING: ContextVar[bool] = ContextVar("active_resuming", default=False)

_ACTIVE_ROLE_SNAPSHOTS: ContextVar[dict[str, object] | None] = ContextVar("active_role_snapshots", default=None)

_CORE_WRITE_OPERATIONAL_OUTPUTS = _core.write_operational_outputs

_CORE_SELECT_ISSUE = _core.select_issue

_CORE_FETCH_ISSUE_TEXT = _core.fetch_issue_text

_CORE_ENSURE_CLEAN_WORKTREE = _core.ensure_clean_worktree

_CORE_ENSURE_ISSUE_BRANCH = _core.ensure_issue_branch

_CORE_CREATE_DRAFT_PR = _core.create_draft_pr

class _DeferredProvider(ModelProvider):
    def __init__(self, config: ModelConfig, factory: Callable[[ModelConfig], ModelProvider]):
        self.config = config
        self.factory = factory
        self.provider: ModelProvider | None = None

    def invoke(self, prompt: str, *, model: str, timeout_seconds: int) -> ProviderResponse:
        if self.provider is None:
            self.provider = self.factory(self.config)
        return self.provider.invoke(prompt, model=model, timeout_seconds=timeout_seconds)

    def generate(self, prompt: str, *, model: str, timeout_seconds: int) -> str:
        return self.invoke(prompt, model=model, timeout_seconds=timeout_seconds).text

def _sync_manifest_invocations(out_dir: Path) -> None:
    path = _ACTIVE_MANIFEST.get()
    if path is not None and path.is_file():
        sync_invocations(path, out_dir / "model-invocations.json")

def _active_args():
    args = _ACTIVE_ARGS.get()
    if args is None:
        raise RunnerError("run context is unavailable", 2)  # noqa: F405
    return args

def _active_manifest_path() -> Path:
    path = _ACTIVE_MANIFEST.get()
    if path is None:
        args = _active_args()
        path = Path(args.out).expanduser().resolve() / MANIFEST_NAME
    return path

def _active_manifest_data() -> dict[str, object]:
    return load_manifest(_active_manifest_path())

def _stage_details(manifest: dict[str, object], stage: str) -> dict[str, object]:
    stages = manifest.get("stages", {})
    record = stages.get(stage, {}) if isinstance(stages, dict) else {}
    details = record.get("details", {}) if isinstance(record, dict) else {}
    return dict(details) if isinstance(details, dict) else {}

def _stage_output_hash(manifest: dict[str, object], stage: str) -> str:
    stages = manifest.get("stages", {})
    record = stages.get(stage, {}) if isinstance(stages, dict) else {}
    return str(record.get("output_hash", "")) if isinstance(record, dict) else ""

def _file_hash_or_empty(path: Path) -> str:
    return hash_file(path) if path.is_file() else ""

def _roles_or_legacy(reader_config, coder_config):
    roles = _ACTIVE_ROLES.get()
    if roles is not None:
        return roles
    return {
        "reader": reader_config,
        "synthesizer": reader_config,
        "planner": coder_config,
        "implementer": coder_config,
        "fixer": coder_config,
        "verifier": None,
    }

def _policies_or_default() -> dict[str, str]:
    return _ACTIVE_POLICIES.get() or resolve_prompt_policies({})

def _semantic_settings_or_disabled() -> SemanticSettings:
    return _ACTIVE_SEMANTIC.get() or SemanticSettings(False)
