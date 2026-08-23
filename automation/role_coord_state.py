from __future__ import annotations

import hashlib
from pathlib import Path
from automation import (
    opencode_adapter,
    opencode_runtime,
    role_resume,
    role_runtime,
    role_runtime_diagnostics,
    workflow_stages,
)
from automation import coordination_contract, coordination_state


def _issue_number(repo: Path, arguments: str = "") -> int:
    return coordination_state.issue_number(repo, arguments)

def role_acceptance(repo: Path, role: str) -> dict[str, object]:
    return coordination_state.role_acceptance(repo, role)

def _role_output_path(repo: Path, role: str) -> Path | None:
    return coordination_state.role_output_path(repo, role)

def _prepare_role(repo: Path, role: str, *, repair_kind: str = "") -> None:
    if role == "implementer":
        return
    issue = _issue_number(repo)
    arguments = f"{issue} {repair_kind}".strip() if repair_kind else str(issue)
    opencode_adapter.prepare_role(role, repo, arguments)
