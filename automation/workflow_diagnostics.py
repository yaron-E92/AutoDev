from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from automation.semantic_verifier import (
    SemanticVerifierError,
    extract_acceptance_criteria,
    parse_semantic_output,
    prepare_semantic_repair_prompt,
    render_template,
)
from automation.workflow_contract import (
    CURRENT_DIR,
    DIAGNOSTICS_FILE,
    FAILURE_DETERMINISTIC,
    WorkflowStageError,
    _exception_classification,
    concise,
)
from automation.workflow_storage import (
    _file_sha256,
    read_json,
    write_json,
)
from automation.workflow_workspace import (
    repository_modified,
    workspace_snapshot,
)

def stage_payload(
    repo: Path,
    outcome: str,
    stage: str,
    *,
    reason: str = "",
    artifact: Path | None = None,
    requested_issue: int = 0,
    next_action: str = "",
    max_repair_attempts: int | None = None,
    max_semantic_repair_attempts: int | None = None,
    failure_classification: str = "",
    failure_fingerprint: str = "",
    repeated_failure: bool = False,
) -> dict[str, object]:
    current = repo / CURRENT_DIR
    state_value = read_json(current / "state.json")
    state = state_value if isinstance(state_value, dict) else {}
    ci_proof = state.get("CiProof", {})
    ci_state = str(ci_proof.get("state", "")) if isinstance(ci_proof, dict) else ""
    payload: dict[str, object] = {
        "state": outcome,
        "issue_number": int(state.get("IssueNumber", 0) or requested_issue or 0),
        "branch": str(state.get("BranchName", "")),
        "completed_stage": str(state.get("Status", "")),
        "failed_stage": stage if outcome in {"FAILED", "BLOCKED", "REPAIR"} else "",
        "stage": stage,
        "reason": concise(reason),
        "failure_classification": failure_classification,
        "failure_fingerprint": failure_fingerprint,
        "repeated_failure": repeated_failure,
        "artifact_dir": str(current),
        "artifact": str(artifact) if artifact is not None else "",
        "repository_modified": repository_modified(repo, current, state),
        "commit_exists": bool(str(state.get("LastCommitSha", "")).strip()),
        "pr_exists": bool(str(state.get("PrUrl", "")).strip()),
        "pr_url": str(state.get("PrUrl", "")),
        "pr_head_sha": str(state.get("PrHeadSha", "")),
        "verified_source_identity": str(state.get("VerifiedSourceIdentity", "")),
        "created_commit_sha": str(state.get("CreatedCommitSha", "")),
        "created_tree_sha": str(state.get("CreatedTreeSha", "")),
        "ci_state": ci_state,
        "next_action": next_action,
    }
    diagnostics = read_json(current / DIAGNOSTICS_FILE)
    if isinstance(diagnostics, dict):
        payload["diagnostics"] = {
            "role_invocations": diagnostics.get("role_invocations", {}),
            "protocol_correction_attempts": diagnostics.get("protocol_correction_attempts", {}),
            "stage_invocations": diagnostics.get("stage_invocations", {}),
            "repeated_identical_failures": diagnostics.get("repeated_identical_failures", 0),
            "stage_wall_time_ms": diagnostics.get("stage_wall_time_ms", {}),
            "shipment_proof": diagnostics.get("shipment_proof", {}),
        }
    if max_repair_attempts is not None:
        payload["max_repair_attempts"] = max_repair_attempts
    if max_semantic_repair_attempts is not None:
        payload["max_semantic_repair_attempts"] = max_semantic_repair_attempts
    return payload

def record_stage_failure(
    repo: Path,
    stage: str,
    error: BaseException,
    *,
    requested_issue: int = 0,
    next_action: str = "correct the reported setup or deterministic stage failure before retrying",
) -> dict[str, object]:
    repo = repo.expanduser().resolve()
    classification = _exception_classification(error)
    reason = concise(str(error))
    input_fingerprint = _stage_input_fingerprint(repo, stage)
    fingerprint = hashlib.sha256(
        f"{stage}|{classification}|{reason}|{input_fingerprint}".encode("utf-8", errors="replace")
    ).hexdigest()
    current = repo / CURRENT_DIR
    if current.is_dir():
        diagnostics = _diagnostics(current)
        failures = diagnostics.setdefault("failure_fingerprints", {})
        if isinstance(failures, dict):
            failures[fingerprint] = int(failures.get(fingerprint, 0) or 0) + 1
        diagnostics["last_failure"] = {
            "stage": stage,
            "classification": classification,
            "reason": reason,
            "fingerprint": fingerprint,
            "input_fingerprint": input_fingerprint,
        }
        _write_diagnostics(current, diagnostics)
    return stage_payload(
        repo,
        "FAILED",
        stage,
        reason=reason,
        requested_issue=requested_issue,
        next_action=next_action,
        failure_classification=classification,
        failure_fingerprint=fingerprint,
    )

def _require_accepted_role(
    current: Path,
    state: dict[str, object],
    role: str,
    artifact_name: str,
) -> None:
    if not state.get("OpenCodeProtocolVersion"):
        return
    accepted = state.get("AcceptedRoleArtifacts", {})
    entry = accepted.get(role) if isinstance(accepted, dict) else None
    if not isinstance(entry, dict):
        raise WorkflowStageError(
            f"stage prerequisite not met: OpenCode role {role} has not been accepted; "
            f"accept {artifact_name} before continuing"
        )
    artifact = current / artifact_name
    expected = str(entry.get("sha256", ""))
    actual = _file_sha256(artifact)
    if not expected or not actual or expected != actual:
        raise WorkflowStageError(
            f"stage prerequisite not met: accepted {role} artifact {artifact_name} is missing or changed; "
            "rerun the role's exact accept command before continuing"
        )

def _repeat_failure_payload(repo: Path, stage: str) -> tuple[int, dict[str, object]] | None:
    if stage in {"preflight", "prepare", "failed", "blocked", "ready", "status"}:
        return None
    current = repo / CURRENT_DIR
    if not current.is_dir():
        return None
    diagnostics = _diagnostics(current)
    last = diagnostics.get("last_failure", {})
    if not isinstance(last, dict):
        return None
    if last.get("stage") != stage or last.get("classification") != FAILURE_DETERMINISTIC:
        return None
    current_input = _stage_input_fingerprint(repo, stage)
    if not current_input or current_input != str(last.get("input_fingerprint", "")):
        return None
    diagnostics["repeated_identical_failures"] = int(
        diagnostics.get("repeated_identical_failures", 0) or 0
    ) + 1
    fingerprint = str(last.get("fingerprint", ""))
    _write_diagnostics(current, diagnostics)
    return 1, stage_payload(
        repo,
        "FAILED",
        stage,
        reason=str(last.get("reason", "identical deterministic stage failure")),
        failure_classification=FAILURE_DETERMINISTIC,
        failure_fingerprint=fingerprint,
        repeated_failure=True,
        next_action="do not retry this stage unchanged; correct the deterministic workflow/setup state first",
    )

def _stage_input_fingerprint(repo: Path, stage: str) -> str:
    current = repo / CURRENT_DIR
    state_value = read_json(current / "state.json")
    state = state_value if isinstance(state_value, dict) else {}
    if not state and not current.exists():
        return ""
    state_keys = (
        "IssueNumber",
        "Status",
        "BranchName",
        "BaseSha",
        "BaseTreeSha",
        "PreparedSnapshotHash",
        "LastCommitSha",
        "LastCommitSnapshotHash",
        "VerifiedParentSha",
        "VerifiedSourceIdentity",
        "CreatedCommitSha",
        "CreatedTreeSha",
        "PrHeadSha",
        "CiProof",
        "PrUrl",
        "PrNumber",
        "LocalCheck",
        "LastLocalCheckPassed",
        "LastSemanticVerdict",
        "SemanticSourceIdentity",
        "OpenCodeProtocolVersion",
    )
    artifacts = {}
    for name in (
        "issue.md",
        "plan.md",
        "implementer.md",
        "commit-message.txt",
        "verification-result.json",
        "local-repair.md",
        "verification-repair.md",
        "ci-repair.md",
    ):
        digest = _file_sha256(current / name)
        if digest:
            artifacts[name] = digest
    payload = {
        "stage": stage,
        "state": {key: state.get(key) for key in state_keys},
        "accepted_roles": state.get("AcceptedRoleArtifacts", {}),
        "artifacts": artifacts,
        "workspace": workspace_snapshot(repo) if repo.is_dir() else {},
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8", errors="replace")
    ).hexdigest()

def _record_stage_invocation(repo: Path, stage: str) -> bool:
    current = repo / CURRENT_DIR
    if not current.is_dir():
        return False
    diagnostics = _diagnostics(current)
    values = diagnostics.setdefault("stage_invocations", {})
    if isinstance(values, dict):
        values[stage] = int(values.get(stage, 0) or 0) + 1
    _write_diagnostics(current, diagnostics)
    return True

def _record_stage_timing(repo: Path, stage: str, elapsed_ms: int) -> None:
    current = repo / CURRENT_DIR
    if not current.is_dir():
        return
    diagnostics = _diagnostics(current)
    values = diagnostics.setdefault("stage_wall_time_ms", {})
    if isinstance(values, dict):
        entries = values.setdefault(stage, [])
        if isinstance(entries, list):
            entries.append(max(0, elapsed_ms))
    _write_diagnostics(current, diagnostics)

def _record_shipment_diagnostics(current: Path, **values: object) -> None:
    diagnostics = _diagnostics(current)
    shipment = diagnostics.setdefault("shipment_proof", {})
    if isinstance(shipment, dict):
        shipment.update(values)
    _write_diagnostics(current, diagnostics)

def _diagnostics(current: Path) -> dict[str, object]:
    value = read_json(current / DIAGNOSTICS_FILE)
    return value if isinstance(value, dict) else {}

def _write_diagnostics(current: Path, diagnostics: dict[str, object]) -> None:
    try:
        write_json(current / DIAGNOSTICS_FILE, diagnostics)
    except OSError:
        pass
