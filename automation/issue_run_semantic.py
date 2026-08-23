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
from automation.issue_run_checkpoints import (
    _checkpoint_deterministic,
    _checkpoint_patch_applied,
    _checkpoint_semantic,
    _clear_completed_stages,
    _deterministic_matches_current_patch,
    _patch_is_recorded_as_applied,
    _pending_repair_patch,
    apply_patch_file,
)
from automation.issue_run_models import (
    call_coder,
)
from automation.issue_run_session import (
    _active_manifest_path,
    _file_hash_or_empty,
    _semantic_settings_or_disabled,
    _stage_output_hash,
)

def run_semantic_verification_gate(
    *,
    repo: Path,
    out_dir: Path,
    issue_text: str,
    verification,
    roles: dict[str, ModelConfig | None],
    fixer_provider,
    fixer_config,
    factory,
    stream,
):
    settings = _semantic_settings_or_disabled()
    manifest_file = _active_manifest_path()
    manifest = load_manifest(manifest_file)
    if stage_completed(manifest, "semantic-verified"):
        return verification
    if not settings.enabled:
        complete_stage(
            manifest_file,
            "semantic-verified",
            run_root=out_dir,
            inputs={"deterministic_output": _stage_output_hash(manifest, "deterministic-verified")},
            details={"enabled": False, "verdict": "not-configured"},
        )
        return verification

    resumed_repair = _pending_repair_patch(out_dir, manifest, kind="semantic")
    if resumed_repair is not None:
        repair_patch, repair_attempt = resumed_repair
        if not _patch_is_recorded_as_applied(manifest, repair_patch):
            _clear_completed_stages(manifest_file, ["deterministic-verified", "semantic-verified", "pr-created"], "semantic repair patch")
            apply_patch_file(repo, repair_patch, stream)
            _checkpoint_patch_applied(out_dir, repo, repair_patch, stream, attempt=repair_attempt)
            manifest = load_manifest(manifest_file)
        elif not _deterministic_matches_current_patch(manifest):
            _clear_completed_stages(manifest_file, ["deterministic-verified", "semantic-verified", "pr-created"], "semantic repair requires re-verification")
            manifest = load_manifest(manifest_file)
        deterministic_attempt = int(getattr(verification, "attempt", 0)) + 1
        if not stage_completed(manifest, "deterministic-verified"):
            verification = run_recommended_verification(out_dir, repo, deterministic_attempt, stream)  # noqa: F405
            write_verification_result(out_dir, verification)  # noqa: F405
            if not verification.passed:
                record_failure(
                    manifest_file,
                    classification="deterministic_verification_failed",
                    reason="deterministic verification failed after resumed semantic repair",
                    stage="deterministic-verified",
                )
                return verification
            _checkpoint_deterministic(out_dir, repo, verification, stream)
        return _run_final_semantic_attempt(repo, out_dir, issue_text, verification, roles, settings, factory)

    verifier_config = roles.get("verifier")
    if verifier_config is None:
        raise RunnerError("semantic verification is enabled but verifier role is unavailable")  # noqa: F405
    verifier_provider = factory(verifier_config)
    result = _invoke_semantic_attempt(
        repo=repo,
        out_dir=out_dir,
        issue_text=issue_text,
        verifier_provider=verifier_provider,
        verifier_config=verifier_config,
        semantic_attempt=0,
        call_attempt=0,
        settings=settings,
    )
    semantic_path = write_semantic_result(out_dir, 0, result)

    if result["verdict"] == "pass":
        final_path = write_final_verdict(out_dir, result)
        _checkpoint_semantic(out_dir, result, [semantic_path, final_path])
        return verification
    if result["verdict"] == "blocked":
        write_final_verdict(out_dir, result)
        record_stage_state(manifest_file, "semantic-verified", status="blocked", details={"verdict": "blocked"})
        record_failure(
            manifest_file,
            classification="semantic_blocked",
            reason=str(result.get("repair_brief", "semantic verifier blocked the run")),
            stage="semantic-verified",
        )
        raise RunnerError("semantic verification blocked the run: " + str(result.get("repair_brief", "")))  # noqa: F405
    if settings.max_repair_attempts < 1:
        write_final_verdict(out_dir, result)
        record_failure(
            manifest_file,
            classification="semantic_repair_disabled",
            reason="semantic verification requested repair but semantic repair is disabled",
            stage="semantic-verified",
        )
        raise RunnerError("semantic verification requested repair but semantic repair is disabled")  # noqa: F405

    repair_brief_path = out_dir / "verification" / "repair-brief.md"
    write_text(repair_brief_path, str(result.get("repair_brief", "")).strip() + "\n")  # noqa: F405
    changed_files = collect_changed_files(repo)
    repair_prompt = build_semantic_repair_prompt(
        issue_text=issue_text,
        plan=read_optional_text(out_dir / "coder-plan.md"),  # noqa: F405
        semantic_result=result,
        changed_files=changed_files,
        diff=collect_current_diff(repo, changed_files),
        template=read_optional_text(PROMPT_TEMPLATE_DIR / "verification-repair.md"),  # noqa: F405
    )
    semantic_repair_prompt = out_dir / "verification" / "semantic-repair-prompt.md"
    write_text(semantic_repair_prompt, repair_prompt)  # noqa: F405
    if fixer_provider is None:
        fixer_provider = factory(fixer_config)
    repair_response = call_coder(
        fixer_provider,
        fixer_config,
        repair_prompt,
        out_dir,
        0,
        role="fixer",
        response_name="semantic-fixer-attempt-0.txt",
    )
    if parse_no_changes_required(repair_response) is not None:  # noqa: F405
        write_final_verdict(out_dir, result)
        raise RunnerError("semantic fixer returned NO_CHANGES_REQUIRED without a final semantic pass")  # noqa: F405
    patch_text = extract_unified_diff(repair_response)  # noqa: F405
    if not patch_text:
        raise RunnerError("semantic fixer did not return a valid patch")  # noqa: F405
    patch_path = out_dir / "verification" / "semantic-repair-attempt-0.patch"
    write_text(patch_path, patch_text)  # noqa: F405
    complete_stage(
        manifest_file,
        "repair-generated",
        run_root=out_dir,
        artifacts=[repair_brief_path, semantic_repair_prompt, out_dir / "model-responses" / "semantic-fixer-attempt-0.txt", patch_path],
        inputs={
            "semantic_result_sha256": _file_hash_or_empty(semantic_path),
            "fixer_fingerprint": stage_role_fingerprint(load_manifest(manifest_file), "fixer"),
        },
        details={
            "kind": "semantic",
            "attempt": 0,
            "patch_path": patch_path.relative_to(out_dir).as_posix(),
            "patch_hash": hash_file(patch_path),
        },
    )
    _clear_completed_stages(manifest_file, ["deterministic-verified", "semantic-verified", "pr-created"], "semantic repair patch")
    apply_patch_file(repo, patch_path, stream)  # noqa: F405
    _checkpoint_patch_applied(out_dir, repo, patch_path, stream)

    deterministic_attempt = int(getattr(verification, "attempt", 0)) + 1
    verification = run_recommended_verification(out_dir, repo, deterministic_attempt, stream)  # noqa: F405
    write_verification_result(out_dir, verification)  # noqa: F405
    if not verification.passed:
        blocked = dict(result)
        blocked["verdict"] = "blocked"
        blocked["repair_brief"] = "Deterministic verification failed after semantic repair."
        write_final_verdict(out_dir, blocked)
        record_failure(
            manifest_file,
            classification="deterministic_verification_failed",
            reason="deterministic verification failed after semantic repair",
            stage="deterministic-verified",
        )
        return verification
    _checkpoint_deterministic(out_dir, repo, verification, stream)
    return _run_final_semantic_attempt(repo, out_dir, issue_text, verification, roles, settings, factory)

def _run_final_semantic_attempt(repo, out_dir, issue_text, verification, roles, settings, factory):
    verifier_config = roles.get("verifier")
    if verifier_config is None:
        raise RunnerError("semantic verification is enabled but verifier role is unavailable")  # noqa: F405
    verifier_provider = factory(verifier_config)
    final_result = _invoke_semantic_attempt(
        repo=repo,
        out_dir=out_dir,
        issue_text=issue_text,
        verifier_provider=verifier_provider,
        verifier_config=verifier_config,
        semantic_attempt=1,
        call_attempt=settings.max_schema_retries + 1,
        settings=settings,
    )
    semantic_path = write_semantic_result(out_dir, 1, final_result)
    final_path = write_final_verdict(out_dir, final_result)
    if final_result["verdict"] != "pass":
        record_failure(
            _active_manifest_path(),
            classification="semantic_not_passed",
            reason="semantic verification did not pass after the targeted repair",
            stage="semantic-verified",
        )
        raise RunnerError("semantic verification did not pass after the targeted repair")  # noqa: F405
    _checkpoint_semantic(out_dir, final_result, [semantic_path, final_path])
    return verification

def _invoke_semantic_attempt(
    *,
    repo: Path,
    out_dir: Path,
    issue_text: str,
    verifier_provider,
    verifier_config,
    semantic_attempt: int,
    call_attempt: int,
    settings: SemanticSettings,
) -> dict[str, object]:
    changed_files = collect_changed_files(repo)
    prompt = build_semantic_prompt(
        issue_text=issue_text,
        synthesized_handoff=read_optional_text(out_dir / "synthesized-handoff.md"),  # noqa: F405
        plan=read_optional_text(out_dir / "coder-plan.md"),  # noqa: F405
        changed_files=changed_files,
        diff=collect_current_diff(repo, changed_files),
        deterministic_evidence=(
            read_optional_text(out_dir / "verification-result-summary.md")  # noqa: F405
            + "\n\n"
            + read_optional_text(out_dir / "recommended-command-groups.json")  # noqa: F405
        ),
        uncertainty_notes=read_optional_text(out_dir / "verification-notes.md"),  # noqa: F405
        template=read_optional_text(PROMPT_TEMPLATE_DIR / "verifier.md"),  # noqa: F405
    )
    write_text(out_dir / "verification" / f"semantic-prompt-{semantic_attempt}.md", prompt)  # noqa: F405
    current_prompt = prompt
    for schema_attempt in range(settings.max_schema_retries + 1):
        response = call_coder(
            verifier_provider,
            verifier_config,
            current_prompt,
            out_dir,
            call_attempt + schema_attempt,
            role="verifier",
            response_name=f"semantic-verifier-{semantic_attempt}-schema-{schema_attempt}.txt",
        )
        try:
            return parse_semantic_output(response)
        except SemanticVerifierError as exc:
            if schema_attempt >= settings.max_schema_retries:
                record_failure(
                    _active_manifest_path(),
                    classification="malformed_semantic_output",
                    reason=str(exc),
                    stage="semantic-verified",
                )
                raise RunnerError(f"semantic verifier output remained malformed: {exc}") from exc  # noqa: F405
            current_prompt = build_schema_repair_prompt(prompt, response, str(exc))
    raise RunnerError("semantic verifier did not return a valid verdict")  # noqa: F405
