from __future__ import annotations

from automation import opencode_adapter_protocol

import json
import subprocess
from pathlib import Path
from typing import Callable, Mapping
from automation import (
    opencode_runtime,
    role_resume,
    role_runtime,
    role_runtime_diagnostics,
    workflow_stages,
)
from automation import coordination_contract, coordination_state

from automation.role_coordinator_contract import (
    RoleCoordinatorError,
)
from automation.role_coordinator_state import (
    _issue_number,
)

def run_stage(
    repo: Path,
    name: str,
    *,
    runtime_name: str,
    arguments: str = "",
    attempt: int = 0,
    reason: str = "",
    runner: Callable[..., object] = subprocess.run,
    which=None,
) -> dict[str, object]:
    try:
        code, payload = workflow_stages.execute_stage(
            name,
            repo,
            arguments=arguments,
            attempt=attempt,
            reason=reason,
            runner=runner,
            which=which or workflow_stages.shutil.which,
        )
    except workflow_stages.WorkflowStageError as exc:
        role_resume.checkpoint_failure(repo, name, exc)
        raise RoleCoordinatorError(
            str(exc),
            classification=exc.classification,
        ) from exc

    print(json.dumps({"event": "stage", **payload}, sort_keys=True), flush=True)
    current = repo / workflow_stages.CURRENT_DIR
    if name == "prepare" and payload.get("state") == "CONTINUE" and current.is_dir():
        opencode_adapter_protocol._ensure_opencode_protocol(current)
        role_resume.create_manifest(
            repo,
            workflow_stages.read_state(current),
            runtime_name=runtime_name,
        )
        role_runtime.persist_selection(
            repo,
            name=runtime_name,
            source="selected",
            force_manifest=True,
        )
    elif name == "render-implementer" and payload.get("state") == "CONTINUE" and current.is_dir():
        opencode_adapter_protocol._ensure_opencode_protocol(current)
        opencode_adapter_protocol._begin_role_invocation(current, "implementer")
    if name != "prepare" and role_resume.has_manifest(repo):
        role_resume.checkpoint_stage(repo, name, payload, attempt)
    if code != 0 and payload.get("state") not in {"FAILED", "BLOCKED", "REPAIR"}:
        raise RoleCoordinatorError(
            str(payload.get("reason", ""))
            or f"AutoDev stage {name} failed with exit code {code}"
        )
    return payload

def terminal_payload(
    repo: Path,
    payload: dict[str, object],
    *,
    arguments: str = "",
) -> dict[str, object]:
    state = str(payload.get("state", "FAILED"))
    if state == "PR_READY":
        return dict(payload)
    current = repo / workflow_stages.CURRENT_DIR
    reason = str(payload.get("reason", "AutoDev workflow stopped"))
    issue = int(payload.get("issue_number", 0) or _issue_number(repo, arguments))
    if state == "BLOCKED":
        try:
            workflow_stages.mark_blocked(
                current,
                workflow_stages.read_state(current),
                reason,
            )
        except (OSError, ValueError, workflow_stages.WorkflowStageError):
            pass
        if role_resume.has_manifest(repo):
            role_resume.checkpoint_failure(
                repo,
                str(payload.get("failed_stage", "blocked")),
                RoleCoordinatorError(
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

    failure = RoleCoordinatorError(
        reason,
        classification=str(
            payload.get("failure_classification", "")
            or workflow_stages.FAILURE_DETERMINISTIC
        ),
        diagnostic_path=str(payload.get("artifact", "")),
    )
    if role_resume.has_manifest(repo):
        role_resume.checkpoint_failure(
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
        next_action="inspect the reported failure, correct it, then resume AutoDev",
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
    snapshots: dict[str, object],
    *,
    invalidated_roles: set[str] | None = None,
    runner: Callable[..., object] = subprocess.run,
) -> dict[str, object]:
    return role_resume.resume(
        repo,
        snapshots,
        invalidated_roles=invalidated_roles or set(),
        runner=runner,
    )
