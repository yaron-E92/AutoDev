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
    _active_manifest_path,
    _file_hash_or_empty,
    _stage_details,
    _stage_output_hash,
)

def apply_patch_file(repo: Path, patch_path: Path, stream: TextIO) -> bool:
    reverse = run_command(["git", "apply", "--check", "--reverse", str(patch_path)], cwd=repo, stream=stream, check=False)  # noqa: F405
    if reverse.returncode == 0:
        return False
    result = run_command(["git", "apply", "--index", str(patch_path)], cwd=repo, stream=stream, check=False)  # noqa: F405
    if result.returncode == 0:
        return True
    fallback = run_command(["git", "apply", str(patch_path)], cwd=repo, stream=stream, check=False)  # noqa: F405
    if fallback.returncode != 0:
        raise RunnerError("patch application failed\n" + format_command_failure(fallback))  # noqa: F405
    return True

def _checkpoint_patch_applied(
    out_dir: Path,
    repo: Path,
    patch: Path | None,
    stream: TextIO,
    *,
    attempt: int = 0,
    no_changes: bool = False,
) -> None:
    diff_text = run_command(["git", "diff", "--binary", "HEAD"], cwd=repo, stream=stream, check=False).stdout  # noqa: F405
    changed_paths = changed_worktree_paths(repo, stream)  # noqa: F405
    artifacts = [patch] if patch is not None else []
    complete_stage(
        _active_manifest_path(),
        "patch-applied",
        run_root=out_dir,
        artifacts=artifacts,
        inputs={"patch_hash": hash_file(patch) if patch is not None else "no-changes"},
        details={
            "attempt": attempt,
            "no_changes": no_changes,
            "last_patch_hash": hash_file(patch) if patch is not None else "",
            "worktree_hash": hash_text(diff_text),
            "changed_paths": changed_paths,
        },
    )

def _checkpoint_deterministic(
    out_dir: Path,
    repo: Path,
    verification,
    stream: TextIO,
    *,
    no_changes: bool = False,
) -> None:
    diff_text = run_command(["git", "diff", "--binary", "HEAD"], cwd=repo, stream=stream, check=False).stdout  # noqa: F405
    complete_stage(
        _active_manifest_path(),
        "deterministic-verified",
        run_root=out_dir,
        artifacts=[verification.summary_path, out_dir / "verification-result-summary.md"],
        inputs={
            "patch_output": _stage_output_hash(load_manifest(_active_manifest_path()), "patch-applied"),
            "verification_groups_sha256": _file_hash_or_empty(out_dir / "recommended-command-groups.json"),
        },
        details={
            "attempt": verification.attempt,
            "returncode": verification.returncode,
            "command_group": verification.command_group,
            "worktree_hash": hash_text(diff_text),
            "no_changes": no_changes,
        },
    )

def _pending_repair_patch(out_dir: Path, manifest: dict[str, object], *, kind: str) -> tuple[Path, int] | None:
    if not stage_completed(manifest, "repair-generated"):
        return None
    details = _stage_details(manifest, "repair-generated")
    if details.get("kind") != kind:
        return None
    relative = str(details.get("patch_path", ""))
    if not relative:
        return None
    return out_dir / relative, int(details.get("attempt", 0))

def _patch_is_recorded_as_applied(manifest: dict[str, object], patch: Path) -> bool:
    if not stage_completed(manifest, "patch-applied"):
        return False
    return str(_stage_details(manifest, "patch-applied").get("last_patch_hash", "")) == hash_file(patch)

def _next_fix_attempt(out_dir: Path) -> int:
    attempts = [0]
    for path in (out_dir / "model-patches").glob("attempt-*.*"):
        match = re.match(r"attempt-(\d+)", path.name)
        if match:
            attempts.append(int(match.group(1)))
    return max(attempts) + 1

def _resumed_verification(out_dir: Path) -> VerificationResult:
    return VerificationResult(  # noqa: F405
        0,
        0,
        "resumed",
        read_optional_text(out_dir / "verification-result-summary.md"),  # noqa: F405
        "",
        out_dir / "verification-result-summary.md",
    )

def _deterministic_matches_current_patch(manifest: dict[str, object]) -> bool:
    if not stage_completed(manifest, "deterministic-verified") or not stage_completed(manifest, "patch-applied"):
        return False
    deterministic_hash = str(_stage_details(manifest, "deterministic-verified").get("worktree_hash", ""))
    patch_hash = str(_stage_details(manifest, "patch-applied").get("worktree_hash", ""))
    return bool(deterministic_hash and deterministic_hash == patch_hash)

def _clear_completed_stages(path: Path, stages_to_clear: list[str], reason: str) -> None:
    manifest = load_manifest(path)
    stages = manifest.get("stages", {})
    completed = manifest.get("completed_stages", [])
    invalidations = manifest.setdefault("invalidations", [])
    if not isinstance(stages, dict) or not isinstance(completed, list) or not isinstance(invalidations, list):
        raise ManifestError("run manifest stage state is invalid")
    for stage in stages_to_clear:
        record = stages.pop(stage, None)
        if stage in completed:
            completed.remove(stage)
        if record is not None:
            invalidations.append(
                {
                    "stage": stage,
                    "role": "workflow",
                    "reason": reason,
                    "invalidated_at": datetime.now(timezone.utc).isoformat(),
                    "previous_output_hash": record.get("output_hash", "") if isinstance(record, dict) else "",
                }
            )
    manifest["current_stage"] = completed[-1] if completed else ""
    manifest["failure"] = {}
    save_manifest(path, manifest)

def _checkpoint_semantic(out_dir: Path, result: dict[str, object], artifacts: list[Path]) -> None:
    manifest = load_manifest(_active_manifest_path())
    complete_stage(
        _active_manifest_path(),
        "semantic-verified",
        run_root=out_dir,
        artifacts=artifacts,
        inputs={
            "deterministic_output": _stage_output_hash(manifest, "deterministic-verified"),
            "verifier_fingerprint": stage_role_fingerprint(manifest, "verifier"),
        },
        details={"enabled": True, "verdict": result.get("verdict", "")},
    )
