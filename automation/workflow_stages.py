from __future__ import annotations

import copy
import os
import sys
from pathlib import Path

from automation import semantic_repair_budget as _semantic_budget
from automation import workspace_scope
from automation import workflow_stages_core as _core


def _workspace_snapshot(repo: Path) -> dict[str, str]:
    try:
        return workspace_scope.workspace_snapshot(
            repo,
            fallback_ignored=_core.ignored_workspace_path,
        )
    except workspace_scope.WorkspaceScopeError as exc:
        raise _core.WorkflowStageError(str(exc)) from exc


def _workspace_file_paths(repo: Path) -> list[str]:
    try:
        return workspace_scope.workspace_paths(
            repo,
            fallback_ignored=_core.ignored_workspace_path,
        )
    except workspace_scope.WorkspaceScopeError as exc:
        raise _core.WorkflowStageError(str(exc)) from exc


def _workspace_path_in_scope(repo: Path, relative: str) -> bool:
    try:
        return workspace_scope.path_is_in_scope(
            repo,
            relative,
            fallback_ignored=_core.ignored_workspace_path,
        )
    except workspace_scope.WorkspaceScopeError as exc:
        raise _core.WorkflowStageError(str(exc)) from exc


_original_create_api_commit = _core.create_api_commit
_original_execute_stage = _core.execute_stage
_original_mark_blocked = _core.mark_blocked


def _resume_semantic_budget(
    existing: dict[str, object],
    *,
    attempt: int,
    fixed_default: int,
) -> dict[str, object]:
    """Resume a persisted budget without mistaking inherited defaults for consent.

    OpenCode normally injects MAX_SEMANTIC_REPAIR_ATTEMPTS into every run. An
    unchanged inherited value is therefore not evidence that the user raised a
    previously exhausted adaptive budget. Persist the value observed when the
    budget was created and only treat a later strictly larger value as an
    explicit monotonic increase.
    """

    budget = copy.deepcopy(existing)
    previous = int(budget.get("effective_limit", 0) or 0)
    effective = max(previous, attempt)

    observed = int(
        budget.get(
            "fixed_limit_observed",
            budget.get("configured_limit", fixed_default),
        )
        or 0
    )
    current_fixed = _semantic_budget._nonnegative_int(  # type: ignore[attr-defined]
        _semantic_budget.FIXED_LIMIT_ENV,
        observed,
    )
    budget.setdefault("fixed_limit_observed", observed)
    if current_fixed > observed:
        budget["fixed_limit_observed"] = current_fixed
        if current_fixed > effective:
            effective = current_fixed
            budget["manual_limit_increase"] = current_fixed

    if str(budget.get("policy", "")) == "adaptive":
        old_cap = int(
            budget.get("max_attempts", _semantic_budget.DEFAULT_ADAPTIVE_MAX) or 0
        )
        new_cap = _semantic_budget._nonnegative_int(  # type: ignore[attr-defined]
            _semantic_budget.ADAPTIVE_MAX_ENV,
            old_cap,
        )
        if new_cap > old_cap:
            raw_attempts = int(budget.get("raw_attempts", 0) or 0)
            minimum = int(
                budget.get("min_attempts", _semantic_budget.DEFAULT_ADAPTIVE_MIN)
                or 0
            )
            recomputed = min(new_cap, max(minimum, raw_attempts))
            if recomputed > effective:
                effective = recomputed
            budget["max_attempts"] = new_cap
            budget["adaptive_cap_increased_from"] = old_cap

    budget["effective_limit"] = effective
    budget["attempts_consumed"] = attempt
    if (
        budget.get("policy") == "adaptive"
        and attempt > int(budget.get("max_attempts", effective) or effective)
    ):
        budget["cap_exceeded_by_consumed_attempts"] = True
    return budget


# Replace the internal persisted-budget resolver before any workflow/status/resume
# call uses it. This keeps the policy boundary in one place while preserving the
# public semantic_repair_budget API.
_semantic_budget._resume_budget = _resume_semantic_budget  # type: ignore[attr-defined]


def _create_api_commit(
    repo: Path,
    state: dict[str, object],
    changes: list[dict[str, str]],
    current: Path,
    *,
    runner=_core.subprocess.run,
) -> str:
    scoped = set(_workspace_file_paths(repo))
    baseline_path, _ = _core._baseline_snapshot(current, state)
    baseline = _core.read_json(baseline_path)
    baseline_paths = set(str(path) for path in baseline) if isinstance(baseline, dict) else set()
    outside = sorted(
        str(change.get("Path", ""))
        for change in changes
        if str(change.get("Path", "")) not in scoped
        and not (
            str(change.get("Status", "")) == "deleted"
            and str(change.get("Path", "")) in baseline_paths
        )
    )
    if outside:
        raise _core.WorkflowStageError(
            "API commit refused because changed paths are outside Git's tracked/nonignored workspace scope: "
            + ", ".join(outside[:20])
        )
    return _original_create_api_commit(
        repo,
        state,
        changes,
        current,
        runner=runner,
    )


def _execute_stage(
    name: str,
    repo: Path,
    *,
    arguments: str = "",
    autodev_root: Path = _core.AUTODEV_ROOT,
    attempt: int = 0,
    reason: str = "",
    runner=_core.subprocess.run,
    which=_core.shutil.which,
) -> tuple[int, dict[str, object]]:
    repo = repo.expanduser().resolve()
    if name == "preflight":
        try:
            _semantic_budget.validate_config(
                fixed_default=_core.DEFAULT_MAX_SEMANTIC_REPAIR_ATTEMPTS
            )
        except _semantic_budget.SemanticRepairBudgetError as exc:
            raise _core.WorkflowStageError(str(exc)) from exc

    if name != "semantic":
        return _original_execute_stage(
            name,
            repo,
            arguments=arguments,
            autodev_root=autodev_root,
            attempt=attempt,
            reason=reason,
            runner=runner,
            which=which,
        )

    current = repo / _core.CURRENT_DIR
    state = _core.read_state(current)
    try:
        budget = _semantic_budget.resolve_budget(
            repo,
            state,
            attempt=attempt,
            fixed_default=_core.DEFAULT_MAX_SEMANTIC_REPAIR_ATTEMPTS,
            runner=runner,
        )
    except _semantic_budget.SemanticRepairBudgetError as exc:
        raise _core.WorkflowStageError(str(exc)) from exc

    if "fixed_limit_observed" not in budget:
        raw_observed = os.environ.get(_semantic_budget.FIXED_LIMIT_ENV, "").strip()
        try:
            observed = (
                int(raw_observed)
                if raw_observed
                else _core.DEFAULT_MAX_SEMANTIC_REPAIR_ATTEMPTS
            )
        except ValueError as exc:
            raise _core.WorkflowStageError(
                f"{_semantic_budget.FIXED_LIMIT_ENV} must be an integer"
            ) from exc
        budget["fixed_limit_observed"] = observed
    _semantic_budget.persist_budget(repo, state, budget)

    previous_limit = os.environ.get(_semantic_budget.FIXED_LIMIT_ENV)
    os.environ[_semantic_budget.FIXED_LIMIT_ENV] = str(
        int(budget.get("effective_limit", 0) or 0)
    )
    try:
        code, payload = _original_execute_stage(
            name,
            repo,
            arguments=arguments,
            autodev_root=autodev_root,
            attempt=attempt,
            reason=reason,
            runner=runner,
            which=which,
        )
    finally:
        if previous_limit is None:
            os.environ.pop(_semantic_budget.FIXED_LIMIT_ENV, None)
        else:
            os.environ[_semantic_budget.FIXED_LIMIT_ENV] = previous_limit

    payload["semantic_repair_budget"] = budget
    payload["semantic_repair_attempt"] = attempt

    result_path = current / "verification-result.json"
    issue_text = _core.read_text(current / "issue.md") or str(state.get("IssueText", ""))
    try:
        result = _core.parse_semantic_output(
            _core.read_text(result_path),
            expected_criteria=_core.extract_acceptance_criteria(issue_text) or None,
        )
    except _core.SemanticVerifierError:
        return code, payload

    if payload.get("state") == "BLOCKED" and result.get("verdict") == "repair":
        repair_path = current / "verification-repair.md"
        if workspace_scope.is_git_worktree(repo):
            _core.prepare_semantic_repair_prompt(
                repo,
                current,
                Path(autodev_root).expanduser().resolve()
                / "promptTemplates"
                / "semantic-repair.md",
                repair_path,
            )
        else:
            # Non-Git unit-test/fixture repositories intentionally retain their
            # filesystem fallback. Preserve the actionable final repair brief
            # without pretending Git-backed semantic evidence was available.
            repair_brief = str(result.get("repair_brief", "")).strip()
            _core.write_text(
                repair_path,
                "# Semantic repair\n\n"
                + (repair_brief or "Review the final semantic verification result.")
                + "\n",
            )
        state = _core.read_state(current)
        details = _semantic_budget.failure_details(
            result,
            budget,
            attempt=attempt,
            verification_result=Path(".autodev-run/current/verification-result.json"),
            repair_artifact=Path(".autodev-run/current/verification-repair.md"),
            verified_source_identity=str(state.get("VerifiedSourceIdentity", "")),
        )
        payload.update(
            {
                "reason": _semantic_budget.concise_failure_reason(details),
                "failure_classification": _semantic_budget.FAILURE_REPAIR_BUDGET_EXHAUSTED,
                "root_failure_classification": _semantic_budget.ROOT_FAILURE_CLASSIFICATION,
                "failure_fingerprint": str(details.get("failure_fingerprint", "")),
                "repair_brief": str(details.get("repair_brief", "")),
                "semantic_requirements": details.get("requirements", []),
                "semantic_findings": details.get("findings", []),
                "verification_result": str(details.get("verification_result", "")),
                "repair_artifact": str(details.get("repair_artifact", "")),
                "verified_source_identity": str(details.get("verified_source_identity", "")),
            }
        )
        _semantic_budget.persist_failure(repo, state, details)
        return code, payload

    state = _core.read_state(current)
    _semantic_budget.clear_failure_state(repo, state)
    return code, payload


def _mark_blocked(
    current: Path,
    state: dict[str, object],
    reason: str,
    *,
    runner=_core.subprocess.run,
) -> None:
    details = state.get("LastSemanticFailureDetails", {})
    rich_reason = (
        _semantic_budget.human_failure_summary(details, reason)
        if isinstance(details, dict) and details
        else reason
    )
    _original_mark_blocked(current, state, rich_reason, runner=runner)


# Keep the long-standing automation.workflow_stages module API while routing the
# canonical workspace universe through Git and the semantic repair boundary
# through the durable bounded-budget policy. Functions in the implementation
# module resolve these names from their own globals, so replacing them here makes
# source identity, shipment, and semantic repair behavior share the same runtime.
_core.workspace_snapshot = _workspace_snapshot
_core.workspace_file_paths = _workspace_file_paths
_core.workspace_path_in_scope = _workspace_path_in_scope
_core.create_api_commit = _create_api_commit
_core.execute_stage = _execute_stage
_core.mark_blocked = _mark_blocked
_core.FAILURE_REPAIR_BUDGET_EXHAUSTED = _semantic_budget.FAILURE_REPAIR_BUDGET_EXHAUSTED
_semantic_budget.install_run_manifest_hooks()

if __name__ == "__main__":
    raise SystemExit(_core.main())

# Imported callers should receive the implementation module itself so existing
# monkeypatching/tests continue to operate on the globals used by stage functions.
sys.modules[__name__] = _core
