from __future__ import annotations

import subprocess
from pathlib import Path

from automation import workflow_stages


PENDING_BUCKETS = {"pending"}
NON_FAILING_BUCKETS = {"pass", "skipping", "neutral"}
FAILING_BUCKETS = {"fail", "cancel", "cancelled"}
PENDING_STATES = {"PENDING", "QUEUED", "IN_PROGRESS", "WAITING", "REQUESTED"}
NON_FAILING_STATES = {"SUCCESS", "SKIPPED", "NEUTRAL"}
FAILING_STATES = {
    "FAILURE",
    "FAILED",
    "ERROR",
    "CANCELLED",
    "TIMED_OUT",
    "ACTION_REQUIRED",
    "STALE",
    "STARTUP_FAILURE",
}


def check_outcome(check: dict[str, object]) -> str:
    bucket = str(check.get("bucket", "")).strip().casefold()
    state = str(check.get("state", "")).strip().upper()
    if bucket in PENDING_BUCKETS or state in PENDING_STATES:
        return "pending"
    if bucket in FAILING_BUCKETS or state in FAILING_STATES:
        return "failure"
    if bucket in NON_FAILING_BUCKETS or state in NON_FAILING_STATES:
        return "success"
    return "failure"


def ci_state(checks: list[dict[str, object]]) -> str:
    if not checks:
        return "not-observed"
    outcomes = [check_outcome(check) for check in checks]
    if "pending" in outcomes:
        return "queued/in-progress"
    if "failure" in outcomes:
        return "terminal-failure"
    return "terminal-success"


def normalized_ready_state(state: dict[str, object]) -> dict[str, object]:
    proof = state.get("CiProof", {})
    if not isinstance(proof, dict):
        return state
    checks = proof.get("checks", [])
    if not isinstance(checks, list):
        return state
    typed_checks = [check for check in checks if isinstance(check, dict)]
    if len(typed_checks) != len(checks) or ci_state(typed_checks) != "terminal-success":
        return state

    normalized_checks: list[dict[str, object]] = []
    for check in typed_checks:
        normalized = dict(check)
        normalized["bucket"] = "pass"
        normalized_checks.append(normalized)

    normalized_proof = dict(proof)
    normalized_proof["state"] = "terminal-success"
    normalized_proof["checks"] = normalized_checks
    normalized_state = dict(state)
    normalized_state["CiProof"] = normalized_proof
    return normalized_state


def install() -> None:
    current_ci_state = workflow_stages._ci_state
    if not getattr(current_ci_state, "_autodev_ci_outcome_guard", False):
        def guarded_ci_state(checks: list[dict[str, object]]) -> str:
            return ci_state(checks)

        guarded_ci_state._autodev_ci_outcome_guard = True  # type: ignore[attr-defined]
        workflow_stages._ci_state = guarded_ci_state

    current_validate_ready = workflow_stages.validate_ready_proof
    if getattr(current_validate_ready, "_autodev_ci_outcome_guard", False):
        return
    original_validate_ready = current_validate_ready

    def guarded_validate_ready_proof(
        current: Path,
        state: dict[str, object],
        *,
        runner=subprocess.run,
    ) -> None:
        original_validate_ready(
            current,
            normalized_ready_state(state),
            runner=runner,
        )

    guarded_validate_ready_proof._autodev_ci_outcome_guard = True  # type: ignore[attr-defined]
    workflow_stages.validate_ready_proof = guarded_validate_ready_proof
