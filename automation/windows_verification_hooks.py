from __future__ import annotations

from pathlib import Path

from automation.windows_verification_contract import (
    MANIFEST_STAGE,
    REPAIR_FILE,
    RESULT_FILE,
)
from automation.windows_verification_manifest import (
    _verification_head,
    install_manifest_hooks,
    payload_metadata,
    proof_current,
    sync_manifest,
    windows_required,
)

def install_opencode_hooks() -> None:
    install_manifest_hooks()
    from automation import opencode_adapter, opencode_coordinator, opencode_resume, run_manifest, workflow_stages

    if getattr(opencode_resume, "_autodev_windows_hooks_installed", False):
        return

    opencode_resume.REPAIR_STAGE_KIND[MANIFEST_STAGE] = "windows"
    opencode_coordinator.REPAIR_KINDS["fixer-windows"] = "windows"

    original_repair_kind = opencode_resume._repair_kind
    original_fixer_source = opencode_adapter._fixer_source
    original_resume_action = opencode_resume.resume_action
    original_checkpoint_stage = opencode_resume.checkpoint_stage
    original_status_text = opencode_resume.status_text
    original_resume = opencode_resume.resume

    def repair_kind(arguments: str) -> str:
        if "windows" in (arguments or "").casefold():
            return "windows"
        return original_repair_kind(arguments)

    def fixer_source(current: Path, arguments: str) -> Path:
        if "windows" in (arguments or "").casefold():
            path = current / REPAIR_FILE
            if path.is_file():
                return path
            raise opencode_adapter.OpenCodeAdapterError(
                f"Windows repair artifact is missing: .autodev-run/current/{REPAIR_FILE}"
            )
        return original_fixer_source(current, arguments)

    def resume_action(manifest: dict[str, object], state: dict[str, object]) -> str:
        action = original_resume_action(manifest, state)
        if action.startswith("fixer-"):
            return action
        if windows_required(state) and run_manifest.stage_completed(manifest, "pr-created"):
            if not run_manifest.stage_completed(manifest, MANIFEST_STAGE) or not proof_current(state):
                return "pr-and-ci"
        return action

    def checkpoint_stage(repo: Path, name: str, payload: dict[str, object], attempt: int) -> None:
        if name != "pr-and-ci" or payload.get("platform_verification_stage") != MANIFEST_STAGE:
            original_checkpoint_stage(repo, name, payload, attempt)
            return

        path = opencode_resume.manifest_path(repo)
        current = repo.expanduser().resolve() / workflow_stages.CURRENT_DIR
        state = workflow_stages.read_state(current)
        outcome = str(payload.get("state", ""))
        windows_attempt = int(payload.get("windows_repair_attempt", 0) or 0)
        failed_stage = str(payload.get("failed_stage", ""))
        windows_success = bool(payload.get("windows_stage_completed")) and proof_current(state)

        if failed_stage != "windows-verification":
            original_checkpoint_stage(repo, name, payload, attempt)

        if windows_success:
            artifacts = [current / RESULT_FILE] if (current / RESULT_FILE).is_file() else []
            run_manifest.complete_stage(
                path,
                MANIFEST_STAGE,
                run_root=current,
                artifacts=artifacts,
                inputs={
                    "head_sha": _verification_head(state),
                    "source_identity": str(state.get("ShippedSourceIdentity", "")),
                },
                details={
                    "attempt": windows_attempt,
                    "state": "terminal-success",
                    "transport": "github-actions",
                    "head_sha": _verification_head(state),
                    "source_identity": str(state.get("ShippedSourceIdentity", "")),
                    "run_id": int((state.get("WindowsVerificationProof", {}) or {}).get("run_id", 0)) if isinstance(state.get("WindowsVerificationProof", {}), dict) else 0,
                    "run_url": str((state.get("WindowsVerificationProof", {}) or {}).get("run_url", "")) if isinstance(state.get("WindowsVerificationProof", {}), dict) else "",
                },
            )
        elif failed_stage == "windows-verification":
            status = "repair-required" if outcome == "REPAIR" else outcome.casefold() or "failed"
            run_manifest.record_stage_state(
                path,
                MANIFEST_STAGE,
                status=status,
                details={
                    "attempt": windows_attempt,
                    "reason": str(payload.get("reason", "")),
                    "failure_classification": str(payload.get("failure_classification", "")),
                    "artifact": str(payload.get("artifact", "")),
                    "head_sha": _verification_head(state),
                    "source_identity": str(state.get("ShippedSourceIdentity", "")),
                },
            )
            if outcome in {"BLOCKED", "FAILED"}:
                run_manifest.record_failure(
                    path,
                    classification=str(payload.get("failure_classification", "workflow_failed")),
                    reason=str(payload.get("reason", "Windows verification stopped")),
                    stage=MANIFEST_STAGE,
                )
        sync_manifest(repo, state)

    def status_text(repo: Path, mappings: dict[str, dict[str, str]], **kwargs) -> str:
        text = original_status_text(repo, mappings, **kwargs).rstrip("\n")
        state = workflow_stages.read_state(repo.expanduser().resolve() / workflow_stages.CURRENT_DIR)
        obligations = state.get("DeferredVerificationObligations", [])
        count = len(obligations) if isinstance(obligations, list) else 0
        windows = sum(
            1
            for item in obligations
            if isinstance(item, dict) and item.get("platform") == "windows"
        ) if isinstance(obligations, list) else 0
        proof = state.get("WindowsVerificationProof", {})
        proof_state = str(proof.get("state", "")) if isinstance(proof, dict) else ""
        run_url = str(proof.get("run_url", "")) if isinstance(proof, dict) else ""
        return (
            text
            + f"\nDeferred verification obligations: {count} (windows={windows})"
            + f"\nWindows verification required: {'yes' if windows_required(state) else 'no'}"
            + f"\nWindows verification transport: GitHub Actions"
            + f"\nWindows verification proof: {proof_state or '(none)'}"
            + (f"\nWindows Actions run: {run_url}" if run_url else "")
            + "\n"
        )

    def resume(repo: Path, mappings: dict[str, dict[str, str]], **kwargs) -> dict[str, object]:
        payload = original_resume(repo, mappings, **kwargs)
        state = workflow_stages.read_state(repo.expanduser().resolve() / workflow_stages.CURRENT_DIR)
        manifest = run_manifest.load_manifest(opencode_resume.manifest_path(repo))
        attempts = opencode_resume.repair_attempts(manifest)
        payload["windows_repair_attempt"] = int(attempts.get("windows", 0) or 0)
        payload.update(payload_metadata(state))
        if payload.get("next_action") == "pr-and-ci" and windows_required(state) and run_manifest.stage_completed(manifest, "pr-created"):
            payload["next_stage"] = MANIFEST_STAGE
        return payload

    opencode_resume._repair_kind = repair_kind
    opencode_adapter._fixer_source = fixer_source
    opencode_resume.resume_action = resume_action
    opencode_resume.checkpoint_stage = checkpoint_stage
    opencode_resume.status_text = status_text
    opencode_resume.resume = resume
    opencode_resume._autodev_windows_hooks_installed = True
