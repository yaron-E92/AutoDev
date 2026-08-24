from __future__ import annotations

from pathlib import Path

from automation import (
    opencode_resume,
    role_coordinator_contract,
    role_coordinator_flow,
    role_resume,
    run_manifest,
    semantic_repair_budget,
    windows_verification,
    workflow_stages,
)


class _CiWaiting(RuntimeError):
    def __init__(self, payload: dict[str, object]) -> None:
        super().__init__(str(payload.get("reason", "required CI is still running")))
        self.payload = dict(payload)


def _install_waiting_bridge() -> None:
    current_run_stage = role_coordinator_flow.run_stage
    if not getattr(current_run_stage, "_autodev_role_ci_waiting", False):
        original_run_stage = current_run_stage

        def run_stage(*args, **kwargs) -> dict[str, object]:
            payload = original_run_stage(*args, **kwargs)
            if payload.get("state") == "WAITING":
                raise _CiWaiting(payload)
            return payload

        run_stage._autodev_role_ci_waiting = True  # type: ignore[attr-defined]
        role_coordinator_flow.run_stage = run_stage

    current_coordinate = role_coordinator_flow.coordinate
    if not getattr(current_coordinate, "_autodev_role_ci_waiting", False):
        original_coordinate = current_coordinate

        def coordinate(*args, **kwargs) -> dict[str, object]:
            try:
                return original_coordinate(*args, **kwargs)
            except _CiWaiting as waiting:
                return dict(waiting.payload)

        coordinate._autodev_role_ci_waiting = True  # type: ignore[attr-defined]
        role_coordinator_flow.coordinate = coordinate


def _install_resume_bridge() -> None:
    current_resume = role_resume.resume
    if getattr(current_resume, "_autodev_role_resume_hooks", False):
        return
    original_resume = current_resume

    def resume(repo: Path, snapshots: dict[str, object], **kwargs) -> dict[str, object]:
        resolved = Path(repo).expanduser().resolve()
        # The legacy OpenCode resume wrapper used to own this reopening hook.
        # It is workflow policy, not runtime policy, so apply it before generic
        # snapshot reconciliation regardless of the selected role runtime.
        semantic_repair_budget.maybe_reopen_exhausted_budget(resolved)
        payload = original_resume(resolved, snapshots, **kwargs)
        semantic_repair_budget._append_resume_metadata(resolved, payload)

        current = resolved / workflow_stages.CURRENT_DIR
        try:
            state = workflow_stages.read_state(current)
            manifest = run_manifest.load_manifest(
                current / run_manifest.MANIFEST_NAME
            )
        except (
            OSError,
            ValueError,
            workflow_stages.WorkflowStageError,
            run_manifest.ManifestError,
        ):
            return payload

        attempts = opencode_resume.repair_attempts(manifest)
        payload["windows_repair_attempt"] = int(attempts.get("windows", 0) or 0)
        payload.update(windows_verification.payload_metadata(state))
        if (
            payload.get("next_action") == "pr-and-ci"
            and windows_verification.windows_required(state)
            and not windows_verification.proof_current(state)
        ):
            payload["next_stage"] = windows_verification.MANIFEST_STAGE
        return payload

    resume._autodev_role_resume_hooks = True  # type: ignore[attr-defined]
    role_resume.resume = resume


def install() -> None:
    # Windows verification extends the existing repair vocabulary. Keep that
    # workflow concern visible to the generic coordinator without teaching the
    # runtime abstraction anything about Windows or GitHub Actions.
    role_coordinator_contract.REPAIR_KINDS.setdefault("fixer-windows", "windows")
    _install_waiting_bridge()
    _install_resume_bridge()
