from __future__ import annotations

from pathlib import Path

from automation.windows_verification_contract import (
    MANIFEST_STAGE,
)

def windows_required(state: dict[str, object]) -> bool:
    return bool(state.get("WindowsVerificationRequired", False))

def _verification_head(state: dict[str, object]) -> str:
    return str(state.get("PrHeadSha", "")).strip() or str(state.get("LastCommitSha", "")).strip()

def proof_current(state: dict[str, object]) -> bool:
    if not windows_required(state):
        return True
    proof = state.get("WindowsVerificationProof")
    if not isinstance(proof, dict) or proof.get("state") != "terminal-success":
        return False
    head = _verification_head(state)
    source = str(state.get("ShippedSourceIdentity", "")).strip()
    return bool(
        head
        and source
        and str(proof.get("head_sha", "")) == head
        and str(proof.get("source_identity", "")) == source
    )

def current_repair_attempt(repo: Path) -> int:
    try:
        from automation import run_manifest

        path = repo.expanduser().resolve() / ".autodev-run" / "current" / run_manifest.MANIFEST_NAME
        if not path.is_file():
            return 0
        manifest = run_manifest.load_manifest(path)
        stages = manifest.get("stages", {})
        record = stages.get(MANIFEST_STAGE, {}) if isinstance(stages, dict) else {}
        details = record.get("details", {}) if isinstance(record, dict) else {}
        return int(details.get("attempt", 0) or 0) if isinstance(details, dict) else 0
    except (OSError, ValueError):
        return 0

def payload_metadata(state: dict[str, object]) -> dict[str, object]:
    proof = state.get("WindowsVerificationProof")
    return {
        "deferred_verification_obligations": state.get("DeferredVerificationObligations", []),
        "windows_verification_required": windows_required(state),
        "windows_verification_proof": proof if isinstance(proof, dict) else {},
    }

def sync_manifest(repo: Path, state: dict[str, object]) -> None:
    try:
        from automation import run_manifest

        path = repo.expanduser().resolve() / ".autodev-run" / "current" / run_manifest.MANIFEST_NAME
        if not path.is_file():
            return
        manifest = run_manifest.load_manifest(path)
        manifest["platform_verification"] = {
            "deferred_obligations": state.get("DeferredVerificationObligations", []),
            "windows_required": windows_required(state),
            "windows_config": state.get("WindowsVerificationConfig", {}),
            "windows_proof": state.get("WindowsVerificationProof", {}),
            "last_windows_failure": state.get("LastWindowsVerificationFailure", {}),
        }
        run_manifest.save_manifest(path, manifest)
    except (OSError, ValueError):
        return

def install_manifest_hooks() -> None:
    from automation import run_manifest

    if MANIFEST_STAGE not in run_manifest.OPTIONAL_STAGES:
        run_manifest.OPTIONAL_STAGES = (*run_manifest.OPTIONAL_STAGES, MANIFEST_STAGE)
        run_manifest.ALL_STAGES = run_manifest.PRIMARY_STAGES + run_manifest.OPTIONAL_STAGES

    if getattr(run_manifest, "_autodev_windows_invalidation_installed", False):
        return
    original = run_manifest.invalidated_stages_for_role

    def invalidated_stages_for_role(manifest: dict[str, object], role: str) -> list[str]:
        affected = list(original(manifest, role))
        completed = set(str(value) for value in manifest.get("completed_stages", []))
        if MANIFEST_STAGE in completed and (
            role == "fixer" or "pr-created" in affected or "semantic-verified" in affected
        ):
            affected.append(MANIFEST_STAGE)
        return affected

    run_manifest.invalidated_stages_for_role = invalidated_stages_for_role
    run_manifest._autodev_windows_invalidation_installed = True
