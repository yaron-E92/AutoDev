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
    _next_fix_attempt,
    _patch_is_recorded_as_applied,
    _pending_repair_patch,
    _resumed_verification,
    apply_patch_file,
)
from automation.issue_run_models import (
    call_coder,
)
from automation.issue_run_semantic import (
    run_semantic_verification_gate,
)
from automation.issue_run_session import (
    _ACTIVE_FACTORY,
    _ACTIVE_MANIFEST,
    _active_manifest_path,
    _file_hash_or_empty,
    _policies_or_default,
    _roles_or_legacy,
    _stage_details,
)

def run_implementation_loop(
    *, repo, out_dir, issue_text, branch_name,
    coder_provider=None, coder_config=None,
    implementer_provider=None, implementer_config=None,
    fixer_provider=None, fixer_config=None,
    max_fix_attempts, dry_run, stream,
):
    if _ACTIVE_MANIFEST.get() is None:
        return _run_uncheckpointed_implementation_loop(
            repo=repo,
            out_dir=out_dir,
            issue_text=issue_text,
            branch_name=branch_name,
            coder_provider=coder_provider,
            coder_config=coder_config,
            implementer_provider=implementer_provider,
            implementer_config=implementer_config,
            fixer_provider=fixer_provider,
            fixer_config=fixer_config,
            max_fix_attempts=max_fix_attempts,
            dry_run=dry_run,
            stream=stream,
        )

    roles = _roles_or_legacy(None, coder_config)
    policies = _policies_or_default()
    implementer_config = implementer_config or roles["implementer"] or coder_config
    fixer_config = fixer_config or roles["fixer"] or implementer_config
    factory = _ACTIVE_FACTORY.get() or create_provider
    manifest_file = _active_manifest_path()
    manifest = load_manifest(manifest_file)

    if stage_completed(manifest, "semantic-verified") and stage_completed(manifest, "deterministic-verified"):
        return _resumed_verification(out_dir)

    patch: Path | None
    if stage_completed(manifest, "implementation-generated"):
        details = _stage_details(manifest, "implementation-generated")
        patch_value = str(details.get("patch_path", ""))
        patch = out_dir / patch_value if patch_value else None
    else:
        implementer_provider = implementer_provider or coder_provider or factory(implementer_config)
        prompt = build_implementation_prompt(  # noqa: F405
            issue_text=issue_text,
            synthesized_handoff=read_optional_text(out_dir / "synthesized-handoff.md"),  # noqa: F405
            coder_plan=read_optional_text(out_dir / "coder-plan.md"),  # noqa: F405
            recommended_command_groups=read_optional_text(out_dir / "recommended-command-groups.json"),  # noqa: F405
            constraints=read_optional_text(PROMPT_TEMPLATE_DIR / "implementer.md"),  # noqa: F405
            branch_name=branch_name,
        )
        prompt = compose_prompt("implementer", prompt, policies["implementer"])
        write_text(out_dir / "implementation-prompt.md", prompt)  # noqa: F405
        response = call_coder(implementer_provider, implementer_config, prompt, out_dir, 0, role="implementer")
        patch = process_model_response(response, out_dir, 0)  # noqa: F405
        response_path = out_dir / "model-responses" / "attempt-0.txt"
        patch_artifact = patch or (out_dir / "model-patches" / "attempt-0.txt")
        complete_stage(
            manifest_file,
            "implementation-generated",
            run_root=out_dir,
            artifacts=[out_dir / "implementation-prompt.md", response_path, patch_artifact],
            inputs={
                "plan_sha256": _file_hash_or_empty(out_dir / "coder-plan.md"),
                "implementer_fingerprint": stage_role_fingerprint(load_manifest(manifest_file), "implementer"),
            },
            details={
                "patch_path": patch.relative_to(out_dir).as_posix() if patch is not None else "",
                "patch_hash": hash_file(patch) if patch is not None else "",
                "no_changes": patch is None,
            },
        )
        manifest = load_manifest(manifest_file)

    if dry_run:
        return VerificationResult(0, 0, "dry-run", "Dry-run implementation did not apply patch.", "", out_dir / "verification" / "attempt-0.md")  # noqa: F405

    if not stage_completed(manifest, "patch-applied"):
        if patch is not None:
            apply_patch_file(repo, patch, stream)
        _checkpoint_patch_applied(out_dir, repo, patch, stream, no_changes=patch is None)
        manifest = load_manifest(manifest_file)

    pending_repair = _pending_repair_patch(out_dir, manifest, kind="deterministic")
    if pending_repair is not None:
        repair_patch, repair_attempt = pending_repair
        if not _patch_is_recorded_as_applied(manifest, repair_patch):
            apply_patch_file(repo, repair_patch, stream)
            _checkpoint_patch_applied(out_dir, repo, repair_patch, stream, attempt=repair_attempt)
            manifest = load_manifest(manifest_file)

    if stage_completed(manifest, "deterministic-verified"):
        verification = _resumed_verification(out_dir)
    elif patch is None and not _stage_details(manifest, "patch-applied").get("last_patch_hash"):
        verification = VerificationResult(0, 0, "no-change", "NO_CHANGES_REQUIRED", "", out_dir / "verification" / "attempt-0.md")  # noqa: F405
        write_verification_attempt(verification)  # noqa: F405
        write_verification_result(out_dir, verification)  # noqa: F405
        _checkpoint_deterministic(out_dir, repo, verification, stream, no_changes=True)
    else:
        attempt = int(_stage_details(manifest, "repair-generated").get("attempt", 0)) if pending_repair else 0
        verification = run_recommended_verification(out_dir, repo, attempt, stream)  # noqa: F405
        write_verification_result(out_dir, verification)  # noqa: F405
        if verification.passed:
            _checkpoint_deterministic(out_dir, repo, verification, stream)
        else:
            record_stage_state(
                manifest_file,
                "deterministic-verified",
                status="failed",
                details={"attempt": attempt, "returncode": verification.returncode, "command_group": verification.command_group},
            )

    attempt = max(1, _next_fix_attempt(out_dir))
    while not verification.passed and attempt <= max_fix_attempts:
        if fixer_provider is None:
            fixer_provider = factory(fixer_config)
        fix_prompt = build_fix_prompt(  # noqa: F405
            issue_text=issue_text,
            synthesized_handoff=read_optional_text(out_dir / "synthesized-handoff.md"),  # noqa: F405
            coder_plan=read_optional_text(out_dir / "coder-plan.md"),  # noqa: F405
            previous_response=read_optional_text(out_dir / "model-responses" / f"attempt-{attempt - 1}.txt"),  # noqa: F405
            current_diff=current_diff(repo, stream),  # noqa: F405
            verification=verification,
        )
        fix_prompt = compose_prompt("fixer", fix_prompt, policies["fixer"])
        write_text(out_dir / "fix-prompt.md", fix_prompt)  # noqa: F405
        response = call_coder(fixer_provider, fixer_config, fix_prompt, out_dir, attempt, role="fixer")
        repair_patch = process_model_response(response, out_dir, attempt)  # noqa: F405
        if repair_patch is None:
            record_failure(
                manifest_file,
                classification="repair_no_changes",
                reason="fixer returned NO_CHANGES_REQUIRED while deterministic verification was failing",
                stage="repair-generated",
            )
            break
        complete_stage(
            manifest_file,
            "repair-generated",
            run_root=out_dir,
            artifacts=[out_dir / "fix-prompt.md", out_dir / "model-responses" / f"attempt-{attempt}.txt", repair_patch],
            inputs={
                "verification_sha256": _file_hash_or_empty(verification.summary_path),
                "fixer_fingerprint": stage_role_fingerprint(load_manifest(manifest_file), "fixer"),
            },
            details={
                "kind": "deterministic",
                "attempt": attempt,
                "patch_path": repair_patch.relative_to(out_dir).as_posix(),
                "patch_hash": hash_file(repair_patch),
            },
        )
        apply_patch_file(repo, repair_patch, stream)
        _checkpoint_patch_applied(out_dir, repo, repair_patch, stream, attempt=attempt)
        verification = run_recommended_verification(out_dir, repo, attempt, stream)  # noqa: F405
        write_verification_result(out_dir, verification)  # noqa: F405
        if verification.passed:
            _checkpoint_deterministic(out_dir, repo, verification, stream)
        else:
            record_stage_state(
                manifest_file,
                "deterministic-verified",
                status="failed",
                details={"attempt": attempt, "returncode": verification.returncode, "command_group": verification.command_group},
            )
        attempt += 1

    if not verification.passed:
        record_failure(
            manifest_file,
            classification="deterministic_verification_failed",
            reason="deterministic verification failed after configured repair attempts",
            stage="deterministic-verified",
        )
        return verification
    return run_semantic_verification_gate(
        repo=repo,
        out_dir=out_dir,
        issue_text=issue_text,
        verification=verification,
        roles=roles,
        fixer_provider=fixer_provider,
        fixer_config=fixer_config,
        factory=factory,
        stream=stream,
    )

def _run_uncheckpointed_implementation_loop(
    *, repo, out_dir, issue_text, branch_name,
    coder_provider=None, coder_config=None,
    implementer_provider=None, implementer_config=None,
    fixer_provider=None, fixer_config=None,
    max_fix_attempts, dry_run, stream,
):
    roles = _roles_or_legacy(None, coder_config)
    policies = _policies_or_default()
    implementer_config = implementer_config or roles["implementer"] or coder_config
    fixer_config = fixer_config or roles["fixer"] or implementer_config
    if implementer_config is None:
        raise RunnerError("implementer configuration is required")  # noqa: F405
    factory = _ACTIVE_FACTORY.get() or create_provider
    implementer_provider = implementer_provider or coder_provider or factory(implementer_config)

    prompt = build_implementation_prompt(  # noqa: F405
        issue_text=issue_text,
        synthesized_handoff=read_optional_text(out_dir / "synthesized-handoff.md"),  # noqa: F405
        coder_plan=read_optional_text(out_dir / "coder-plan.md"),  # noqa: F405
        recommended_command_groups=read_optional_text(out_dir / "recommended-command-groups.json"),  # noqa: F405
        constraints=read_optional_text(PROMPT_TEMPLATE_DIR / "implementer.md"),  # noqa: F405
        branch_name=branch_name,
    )
    prompt = compose_prompt("implementer", prompt, policies["implementer"])
    write_text(out_dir / "implementation-prompt.md", prompt)  # noqa: F405
    response = call_coder(implementer_provider, implementer_config, prompt, out_dir, 0, role="implementer")
    patch = process_model_response(response, out_dir, 0)  # noqa: F405
    if patch is None:
        verification = VerificationResult(0, 0, "no-change", "NO_CHANGES_REQUIRED", "", out_dir / "verification" / "attempt-0.md")  # noqa: F405
        write_verification_result(out_dir, verification)  # noqa: F405
    elif dry_run:
        return VerificationResult(0, 0, "dry-run", "Dry-run implementation did not apply patch.", "", out_dir / "verification" / "attempt-0.md")  # noqa: F405
    else:
        apply_patch_file(repo, patch, stream)
        verification = run_recommended_verification(out_dir, repo, 0, stream)  # noqa: F405
        write_verification_result(out_dir, verification)  # noqa: F405
        attempt = 1
        while not verification.passed and attempt <= max_fix_attempts:
            if fixer_provider is None:
                if fixer_config is None:
                    fixer_config = implementer_config
                fixer_provider = factory(fixer_config)
            fix_prompt = build_fix_prompt(  # noqa: F405
                issue_text=issue_text,
                synthesized_handoff=read_optional_text(out_dir / "synthesized-handoff.md"),  # noqa: F405
                coder_plan=read_optional_text(out_dir / "coder-plan.md"),  # noqa: F405
                previous_response=read_optional_text(out_dir / "model-responses" / f"attempt-{attempt - 1}.txt"),  # noqa: F405
                current_diff=current_diff(repo, stream),  # noqa: F405
                verification=verification,
            )
            fix_prompt = compose_prompt("fixer", fix_prompt, policies["fixer"])
            write_text(out_dir / "fix-prompt.md", fix_prompt)  # noqa: F405
            response = call_coder(fixer_provider, fixer_config, fix_prompt, out_dir, attempt, role="fixer")
            patch = process_model_response(response, out_dir, attempt)  # noqa: F405
            if patch is None:
                break
            apply_patch_file(repo, patch, stream)
            verification = run_recommended_verification(out_dir, repo, attempt, stream)  # noqa: F405
            write_verification_result(out_dir, verification)  # noqa: F405
            attempt += 1
    return verification
