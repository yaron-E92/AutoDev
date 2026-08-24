from __future__ import annotations

import os
from automation import (
    opencode_adapter,
    opencode_runtime,
    role_resume,
    role_runtime,
    role_runtime_diagnostics,
    workflow_stages,
)
from automation import coordination_contract, coordination_state


ROLE_PROMPT = coordination_contract.ROLE_PROMPT

CORRECTION_PROMPT = coordination_contract.CORRECTION_PROMPT

REPAIR_KINDS = coordination_contract.REPAIR_KINDS

ROLE_ACTIONS = coordination_contract.ROLE_ACTIONS

ROLE_TIMEOUT_SECONDS = coordination_contract.ROLE_TIMEOUT_SECONDS

ROLE_TIMEOUT_ENV = "AUTODEV_ROLE_TIMEOUT_SECONDS"

LEGACY_ROLE_TIMEOUT_ENV = "AUTODEV_OPENCODE_ROLE_TIMEOUT_SECONDS"

MAX_TRANSITIONS = coordination_contract.MAX_TRANSITIONS

class RoleCoordinatorError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        classification: str = workflow_stages.FAILURE_DETERMINISTIC,
        diagnostic_path: str = "",
    ) -> None:
        super().__init__(message)
        self.classification = classification
        self.diagnostic_path = diagnostic_path

def role_timeout_seconds(role: str) -> int:
    names = (
        f"AUTODEV_{role.upper()}_ROLE_TIMEOUT_SECONDS",
        f"AUTODEV_OPENCODE_{role.upper()}_TIMEOUT_SECONDS",
        ROLE_TIMEOUT_ENV,
        LEGACY_ROLE_TIMEOUT_ENV,
    )
    selected = next((name for name in names if os.environ.get(name, "").strip()), "")
    if selected:
        raw = os.environ[selected]
        try:
            value = int(raw)
        except ValueError as exc:
            raise RoleCoordinatorError(f"{selected} must be a positive integer") from exc
        if value <= 0:
            raise RoleCoordinatorError(f"{selected} must be a positive integer")
        return value
    return ROLE_TIMEOUT_SECONDS.get(role, 900)

RoleResumeErrorAlias = role_resume.RoleResumeError
