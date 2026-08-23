from __future__ import annotations

import hashlib
import shlex
from pathlib import Path
from typing import TypeVar

from automation import opencode_adapter, workflow_stages


CoordinatorError = TypeVar("CoordinatorError", bound=Exception)


def issue_number(repo: Path, arguments: str = "") -> int:
    try:
        state = workflow_stages.read_state(repo / workflow_stages.CURRENT_DIR)
    except (OSError, ValueError, workflow_stages.WorkflowStageError):
        state = {}
    return int(
        state.get("IssueNumber", 0)
        or workflow_stages.issue_number_from_arguments(arguments)
        or 0
    )


def role_acceptance(repo: Path, role: str) -> dict[str, object]:
    current = repo / workflow_stages.CURRENT_DIR
    try:
        state = workflow_stages.read_state(current)
    except (OSError, ValueError, workflow_stages.WorkflowStageError) as exc:
        return {
            "state": "MISSING",
            "role": role,
            "reason": f"cannot read durable role state: {exc}",
        }
    accepted = state.get("AcceptedRoleArtifacts", {})
    entry = accepted.get(role) if isinstance(accepted, dict) else None
    if not isinstance(entry, dict):
        return {
            "state": "MISSING",
            "role": role,
            "reason": "role has no durable accepted artifact/state",
        }
    artifact = str(entry.get("artifact", ""))
    expected = str(entry.get("sha256", ""))
    if artifact.startswith(".autodev-run/current/"):
        path = current / Path(artifact).name
        try:
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            actual = ""
        if not actual or actual != expected:
            return {
                "state": "STALE",
                "role": role,
                "artifact": artifact,
                "reason": "accepted role artifact is missing or no longer matches its durable hash",
            }
    return {
        "state": "ACCEPTED",
        "role": role,
        "artifact": artifact,
        "sha256": expected,
    }


def role_output_path(repo: Path, role: str) -> Path | None:
    relative = str(opencode_adapter.role_contracts().get(role, {}).get("output_artifact", ""))
    if relative.startswith(".autodev-run/current/"):
        return repo / workflow_stages.CURRENT_DIR / Path(relative).name
    return None


def invalidated_roles(arguments: str, *, roles: tuple[str, ...], error_type: type[CoordinatorError]) -> set[str]:
    tokens = shlex.split(arguments or "")
    values: set[str] = set()
    for index, token in enumerate(tokens):
        if token != "--invalidate-role":
            continue
        if index + 1 >= len(tokens) or tokens[index + 1] not in roles:
            raise error_type("--invalidate-role must be followed by a valid AutoDev role")
        values.add(tokens[index + 1])
    return values
