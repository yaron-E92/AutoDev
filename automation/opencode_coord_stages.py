from __future__ import annotations

import json
from pathlib import Path
from automation import (
    opencode_adapter,
    opencode_cli,
    opencode_resume,
    opencode_runtime,
    privacy,
    role_runtime_diagnostics,
    workflow_stages,
)
from automation import coordination_contract, coordination_state

from automation.opencode_coord_contract import (
    OpenCodeCoordinatorError,
)
from automation.opencode_coord_state import (
    _issue_number,
)

def run_stage(
    repo: Path,
    name: str,
    *,
    arguments: str = "",
    attempt: int = 0,
    reason: str = "",
) -> dict[str, object]:
    code, payload = opencode_adapter.workflow_stage(
        name,
        repo,
        arguments=arguments,
        attempt=attempt,
        reason=reason,
    )
    print(json.dumps({"event": "stage", **payload}, sort_keys=True), flush=True)
    if code != 0 and payload.get("state") not in {"FAILED", "BLOCKED", "REPAIR"}:
        raise OpenCodeCoordinatorError(
            str(payload.get("reason", "")) or f"AutoDev stage {name} failed with exit code {code}"
        )
    return payload

def terminal_payload(repo: Path, payload: dict[str, object], *, arguments: str = "") -> dict[str, object]:
    state = str(payload.get("state", "FAILED"))
    if state == "PR_READY":
        return dict(payload)

    current = repo / workflow_stages.CURRENT_DIR
    reason = str(payload.get("reason", "AutoDev workflow stopped"))
    issue = int(payload.get("issue_number", 0) or _issue_number(repo, arguments))
    if state == "BLOCKED":
        try:
            workflow_stages.mark_blocked(current, workflow_stages.read_state(current), reason)
        except (OSError, ValueError, workflow_stages.WorkflowStageError):
            pass
        if opencode_resume.has_manifest(repo):
            opencode_resume.checkpoint_failure(
                repo,
                str(payload.get("failed_stage", "blocked")),
                OpenCodeCoordinatorError(
                    reason,
                    classification=str(
                        payload.get("failure_classification", "")
                        or workflow_stages.FAILURE_DETERMINISTIC
                    ),
                ),
            )
        result = dict(payload)
        result["state"] = "BLOCKED"
        return result

    failure = OpenCodeCoordinatorError(
        reason,
        classification=str(
            payload.get("failure_classification", "") or workflow_stages.FAILURE_DETERMINISTIC
        ),
        diagnostic_path=str(payload.get("artifact", "")),
    )
    if opencode_resume.has_manifest(repo):
        opencode_resume.checkpoint_failure(
            repo,
            str(payload.get("failed_stage", "python-coordinator")),
            failure,
        )
    result = workflow_stages.stage_payload(
        repo,
        "FAILED",
        str(payload.get("failed_stage", "python-coordinator")),
        reason=reason,
        requested_issue=issue,
        next_action="inspect the reported failure, correct it, then run /autodev-resume",
        failure_classification=failure.classification,
        failure_fingerprint=str(payload.get("failure_fingerprint", "")),
    )
    result["stage"] = "python-coordinator"
    artifact = str(payload.get("artifact", ""))
    if artifact:
        result["artifact"] = artifact
    return result

def _resume_payload(
    repo: Path,
    mappings: dict[str, dict[str, str]],
    *,
    invalidated_roles: set[str] | None = None,
) -> dict[str, object]:
    return opencode_resume.resume(repo, mappings, invalidated_roles=invalidated_roles or set())
