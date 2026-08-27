from __future__ import annotations

from pathlib import Path

from automation.workflow_contract import CURRENT_DIR
from automation.workflow_storage import read_json


PRIMARY_RUN_FIELDS = (
    "branch",
    "completed_stage",
    "commit_exists",
    "pr_exists",
    "pr_url",
    "pr_head_sha",
    "verified_source_identity",
    "created_commit_sha",
    "created_tree_sha",
    "ci_state",
)


def durable_issue_number(repo: Path) -> int:
    state = _state(repo)
    return int(state.get("IssueNumber", 0) or 0)


def is_preserved_prior_run(repo: Path, requested_issue: int) -> bool:
    if requested_issue <= 0:
        return False
    current_issue = durable_issue_number(repo)
    return current_issue > 0 and current_issue != requested_issue


def existing_durable_run(repo: Path) -> dict[str, object]:
    repo = repo.expanduser().resolve()
    current = repo / CURRENT_DIR
    state = _state(repo)
    issue = int(state.get("IssueNumber", 0) or 0)
    if issue <= 0:
        return {}

    ci_proof = state.get("CiProof", {})
    ci_state = str(ci_proof.get("state", "")) if isinstance(ci_proof, dict) else ""
    diagnostics_value = read_json(current / "run-diagnostics.json")
    diagnostics = diagnostics_value if isinstance(diagnostics_value, dict) else {}
    shipment = diagnostics.get("shipment_proof", {})
    shipment_proof = dict(shipment) if isinstance(shipment, dict) else {}

    return {
        "issue_number": issue,
        "status": str(state.get("Status", "")),
        "branch": str(state.get("BranchName", "")),
        "commit_sha": str(state.get("LastCommitSha", "")),
        "pr_url": str(state.get("PrUrl", "")),
        "pr_head_sha": str(state.get("PrHeadSha", "")),
        "created_commit_sha": str(state.get("CreatedCommitSha", "")),
        "created_tree_sha": str(state.get("CreatedTreeSha", "")),
        "verified_source_identity": str(state.get("VerifiedSourceIdentity", "")),
        "ci_state": ci_state,
        "shipment_proof": shipment_proof,
    }


def attribute_explicit_new_run(
    repo: Path,
    payload: dict[str, object],
    requested_issue: int,
) -> dict[str, object]:
    result = dict(payload)
    if requested_issue <= 0:
        return result

    result["requested_issue_number"] = requested_issue
    current_issue = durable_issue_number(repo)
    if current_issue == requested_issue:
        result["issue_number"] = requested_issue
        result["new_run_prepared"] = True
        result.pop("existing_durable_run", None)
        return result

    result["issue_number"] = requested_issue
    result["new_run_prepared"] = False
    result["requested_operation"] = "issue-to-pr"
    result["artifact_dir"] = str(
        repo.expanduser().resolve() / ".autodev-run" / "last-operation"
    )
    result["artifact"] = ""

    for key in PRIMARY_RUN_FIELDS:
        if key in {"commit_exists", "pr_exists"}:
            result[key] = False
        else:
            result[key] = ""

    result["repository_modified"] = False
    result["diagnostics"] = {
        "role_invocations": {},
        "protocol_correction_attempts": {},
        "stage_invocations": {},
        "repeated_identical_failures": 0,
        "stage_wall_time_ms": {},
        "shipment_proof": {},
    }

    previous = existing_durable_run(repo)
    if previous:
        result["existing_durable_run"] = previous
        result["existing_durable_run_preserved"] = True
    else:
        result.pop("existing_durable_run", None)
        result["existing_durable_run_preserved"] = False
    return result


def _state(repo: Path) -> dict[str, object]:
    value = read_json(repo.expanduser().resolve() / CURRENT_DIR / "state.json")
    return value if isinstance(value, dict) else {}
