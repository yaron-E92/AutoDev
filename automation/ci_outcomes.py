from __future__ import annotations

import subprocess
from datetime import datetime, timezone
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


class _CiWaiting(RuntimeError):
    def __init__(self, payload: dict[str, object]) -> None:
        super().__init__(str(payload.get("reason", "required CI is still running")))
        self.payload = dict(payload)


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


def _pending_ci_proof(repo: Path) -> tuple[dict[str, object], dict[str, object]] | None:
    current = repo.expanduser().resolve() / workflow_stages.CURRENT_DIR
    try:
        state = workflow_stages.read_state(current)
    except (OSError, ValueError, workflow_stages.WorkflowStageError):
        return None
    proof = state.get("CiProof", {})
    if not isinstance(proof, dict) or str(proof.get("state", "")) != "queued/in-progress":
        return None
    head_sha = str(proof.get("head_sha", "")).strip()
    expected_head = str(state.get("LastCommitSha", "")).strip()
    pr_head = str(state.get("PrHeadSha", "")).strip()
    if not head_sha or not expected_head or head_sha != expected_head:
        return None
    if pr_head and pr_head != head_sha:
        return None
    return state, dict(proof)


def _persist_poll_budget_exhausted(repo: Path) -> dict[str, object] | None:
    pending = _pending_ci_proof(repo)
    if pending is None:
        return None
    _, proof = pending
    proof["poll_budget_exhausted"] = True
    proof["observed_at"] = datetime.now(timezone.utc).isoformat()
    workflow_stages._persist_ci_proof(
        repo.expanduser().resolve() / workflow_stages.CURRENT_DIR,
        proof,
    )
    return proof


def _waiting_payload(repo: Path, proof: dict[str, object]) -> dict[str, object]:
    repo = repo.expanduser().resolve()
    current = repo / workflow_stages.CURRENT_DIR
    try:
        state = workflow_stages.read_state(current)
    except (OSError, ValueError, workflow_stages.WorkflowStageError):
        state = {}
    head_sha = str(proof.get("head_sha", ""))
    polls = int(proof.get("polls", 0) or 0)
    payload = workflow_stages.stage_payload(
        repo,
        "WAITING",
        "pr-and-ci",
        reason=(
            f"required CI for exact PR head {head_sha or '<missing>'} is still queued or running "
            f"after {polls} poll{'s' if polls != 1 else ''}"
        ),
        artifact=current / "ci-summary.json",
        next_action="wait for CI to finish, then run `python3 .opencode/autodev.py coordinate --resume`",
    )
    payload.update(
        {
            "waiting_reason": "ci-pending",
            "ci_state": str(proof.get("state", "")),
            "ci_polls": polls,
            "pr_head_sha": head_sha,
            "pr_url": str(state.get("PrUrl", "")),
            "commit_sha": str(state.get("LastCommitSha", "")),
        }
    )
    return payload


def _install_waiting_guards() -> None:
    current_wait = workflow_stages.wait_for_required_checks
    if not getattr(current_wait, "_autodev_ci_waiting_guard", False):
        original_wait = current_wait

        def wait_for_required_checks(
            repo: Path,
            state: dict[str, object],
            *,
            runner=subprocess.run,
            sleep=None,
        ) -> dict[str, object]:
            try:
                if sleep is None:
                    return original_wait(repo, state, runner=runner)
                return original_wait(repo, state, runner=runner, sleep=sleep)
            except workflow_stages.WorkflowStageError as exc:
                if exc.classification != workflow_stages.FAILURE_TRANSIENT:
                    raise
                proof = _persist_poll_budget_exhausted(repo)
                if proof is None:
                    raise
                return proof

        wait_for_required_checks._autodev_ci_waiting_guard = True  # type: ignore[attr-defined]
        workflow_stages.wait_for_required_checks = wait_for_required_checks

    current_pr_and_ci = workflow_stages.pr_and_ci
    if not getattr(current_pr_and_ci, "_autodev_ci_waiting_guard", False):
        original_pr_and_ci = current_pr_and_ci

        def pr_and_ci(
            repo: Path,
            current: Path,
            state: dict[str, object],
            autodev_root: Path,
            *,
            runner=subprocess.run,
        ) -> bool | None:
            try:
                return original_pr_and_ci(
                    repo,
                    current,
                    state,
                    autodev_root,
                    runner=runner,
                )
            except workflow_stages.WorkflowStageError as exc:
                if exc.classification != workflow_stages.FAILURE_TRANSIENT:
                    raise
                if _pending_ci_proof(repo) is None:
                    raise
                return None

        pr_and_ci._autodev_ci_waiting_guard = True  # type: ignore[attr-defined]
        workflow_stages.pr_and_ci = pr_and_ci

    current_execute = workflow_stages.execute_stage
    if not getattr(current_execute, "_autodev_ci_waiting_guard", False):
        original_execute = current_execute

        def execute_stage(
            name: str,
            repo: Path,
            **kwargs,
        ) -> tuple[int, dict[str, object]]:
            code, payload = original_execute(name, repo, **kwargs)
            if name != "pr-and-ci":
                return code, payload
            pending = _pending_ci_proof(repo)
            if pending is None:
                return code, payload
            _, proof = pending
            return 0, _waiting_payload(repo, proof)

        execute_stage._autodev_ci_waiting_guard = True  # type: ignore[attr-defined]
        workflow_stages.execute_stage = execute_stage

    from automation import opencode_coordinator

    current_run_stage = opencode_coordinator.run_stage
    if not getattr(current_run_stage, "_autodev_ci_waiting_guard", False):
        original_run_stage = current_run_stage

        def run_stage(*args, **kwargs) -> dict[str, object]:
            payload = original_run_stage(*args, **kwargs)
            if payload.get("state") == "WAITING":
                raise _CiWaiting(payload)
            return payload

        run_stage._autodev_ci_waiting_guard = True  # type: ignore[attr-defined]
        opencode_coordinator.run_stage = run_stage

    current_coordinate = opencode_coordinator.coordinate
    if not getattr(current_coordinate, "_autodev_ci_waiting_guard", False):
        original_coordinate = current_coordinate

        def coordinate(*args, **kwargs) -> dict[str, object]:
            try:
                return original_coordinate(*args, **kwargs)
            except _CiWaiting as waiting:
                return dict(waiting.payload)

        coordinate._autodev_ci_waiting_guard = True  # type: ignore[attr-defined]
        opencode_coordinator.coordinate = coordinate


def install() -> None:
    current_ci_state = workflow_stages._ci_state
    if not getattr(current_ci_state, "_autodev_ci_outcome_guard", False):
        def guarded_ci_state(checks: list[dict[str, object]]) -> str:
            return ci_state(checks)

        guarded_ci_state._autodev_ci_outcome_guard = True  # type: ignore[attr-defined]
        workflow_stages._ci_state = guarded_ci_state

    current_validate_ready = workflow_stages.validate_ready_proof
    if not getattr(current_validate_ready, "_autodev_ci_outcome_guard", False):
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

    _install_waiting_guards()
