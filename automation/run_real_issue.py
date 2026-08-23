from __future__ import annotations

import functools
import inspect

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

from automation import issue_run_session as _overlay_0
from automation import issue_run_resume as _overlay_1
from automation import issue_run_repository as _overlay_2
from automation import issue_run_runtime as _overlay_3
from automation import issue_run_checkpoints as _overlay_4
from automation import issue_run_models as _overlay_5
from automation import issue_run_semantic as _overlay_6
from automation import issue_run_implementation as _overlay_7
from automation import issue_run_pull_request as _overlay_8
from automation import issue_run_entrypoint as _overlay_9

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
    _CORE_CREATE_DRAFT_PR,
    _CORE_ENSURE_CLEAN_WORKTREE,
    _CORE_ENSURE_ISSUE_BRANCH,
    _CORE_FETCH_ISSUE_TEXT,
    _CORE_SELECT_ISSUE,
    _CORE_WRITE_OPERATIONAL_OUTPUTS,
    _DeferredProvider,
    _active_args,
    _active_manifest_data,
    _active_manifest_path,
    _file_hash_or_empty,
    _policies_or_default,
    _roles_or_legacy,
    _semantic_settings_or_disabled,
    _stage_details,
    _stage_output_hash,
    _sync_manifest_invocations,
)

from automation.issue_run_resume import (
    _argument_value,
    _build_role_snapshots,
    _extract_resume_options,
    _inject_resume_arguments,
    _provider_config_path,
    _reconcile_semantic_settings,
    _update_resume_target_options,
    _validate_next_stage_provider,
)

from automation.issue_run_repository import (
    _is_expected_autodev_commit,
    _patch_matches_resume_worktree,
    _patch_paths,
    _pending_uncheckpointed_patch,
    _validate_resume_repository,
    ensure_clean_worktree,
    ensure_issue_branch,
    fetch_issue_text,
    select_issue,
    update_issue_labels,
)

from automation.issue_run_runtime import (
    _refresh_operational_checkpoints,
    resolve_prompt_policy_configs,
    resolve_provider_configs,
    resolve_role_provider_configs,
    resolve_semantic_verification_settings,
    run_area_reader,
    write_operational_outputs,
    write_provider_metadata,
)

from automation.issue_run_checkpoints import (
    _checkpoint_deterministic,
    _checkpoint_patch_applied,
    _checkpoint_semantic,
    _clear_completed_stages,
    _deterministic_matches_current_patch,
    _next_fix_attempt,
    _patch_is_recorded_as_applied,
    _pending_repair_patch,
    _resumed_verification,
    apply_patch_file,
)

from automation.issue_run_models import (
    _stage_for_model_role,
    call_coder,
    write_compression_debug_artifact,
)

from automation.issue_run_semantic import (
    _invoke_semantic_attempt,
    _run_final_semantic_attempt,
    run_semantic_verification_gate,
)

from automation.issue_run_implementation import (
    _run_uncheckpointed_implementation_loop,
    run_implementation_loop,
)

from automation.issue_run_pull_request import (
    _find_existing_pr,
    _record_pr_checkpoint,
    build_pr_body,
    create_draft_pr,
)

from automation.issue_run_entrypoint import (
    main,
    run,
)

_COMPAT_MODULES = (
    _core,
    _overlay_0,
    _overlay_1,
    _overlay_2,
    _overlay_3,
    _overlay_4,
    _overlay_5,
    _overlay_6,
    _overlay_7,
    _overlay_8,
    _overlay_9,
)
_COMPAT_MISSING = object()
_COMPAT_ORIGINALS = {
    module: {
        name: value
        for name, value in module.__dict__.items()
        if name in globals() and not name.startswith("__")
    }
    for module in _COMPAT_MODULES
}
_COMPAT_BASELINE: dict[str, object] = {}


def _sync_compat_overrides() -> None:
    facade = globals()
    for module, originals in _COMPAT_ORIGINALS.items():
        namespace = module.__dict__
        for name, original in originals.items():
            current = facade.get(name, _COMPAT_MISSING)
            if current is _COMPAT_MISSING:
                continue
            baseline = _COMPAT_BASELINE.get(name, _COMPAT_MISSING)
            namespace[name] = original if current is baseline else current


def _compat_entrypoint(target):
    @functools.wraps(target)
    def invoke(*args, **kwargs):
        _sync_compat_overrides()
        return target(*args, **kwargs)
    return invoke


def _install_compat_entrypoints() -> None:
    facade = globals()
    wrapped: set[str] = set()
    for module in _COMPAT_MODULES:
        for name in tuple(module.__dict__):
            if (
                name in wrapped
                or name.startswith("__")
                or name.startswith("_compat")
                or name == "_sync_compat_overrides"
                or name not in facade
            ):
                continue
            value = facade[name]
            if inspect.isfunction(value) and value.__module__.startswith("automation."):
                facade[name] = _compat_entrypoint(value)
                wrapped.add(name)


_install_compat_entrypoints()
_COMPAT_BASELINE.update(globals())


if __name__ == "__main__":
    raise SystemExit(main())
