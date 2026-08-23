from __future__ import annotations

import copy
import functools
import inspect
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from automation import semantic_repair_budget as _semantic_budget
from automation import workspace_scope
from automation import windows_workflow_hooks as _windows_workflow_hooks
from automation import workflow_commands as _workflow_commands
from automation import workflow_contract as _workflow_contract
from automation import workflow_diagnostics as _workflow_diagnostics
from automation import workflow_dispatch as _workflow_dispatch
from automation import workflow_github as _workflow_github
from automation import workflow_preparation as _workflow_preparation
from automation import workflow_prompts as _workflow_prompts
from automation import workflow_storage as _workflow_storage
from automation import workflow_verification as _workflow_verification
from automation import workflow_workspace as _workflow_workspace
from automation.semantic_verifier import (
    SemanticVerifierError,
    extract_acceptance_criteria,
    parse_semantic_output,
    prepare_semantic_repair_prompt,
    render_template,
)

from automation.workflow_contract import (
    AUTODEV_ROOT,
    CURRENT_DIR,
    DEFAULT_CI_CHECK_POLL_ATTEMPTS,
    DEFAULT_CI_CHECK_POLL_SECONDS,
    DEFAULT_MAX_REPAIR_ATTEMPTS,
    DEFAULT_MAX_SEMANTIC_REPAIR_ATTEMPTS,
    DIAGNOSTICS_FILE,
    FAILURE_CODE_REPAIRABLE,
    FAILURE_DETERMINISTIC,
    FAILURE_TRANSIENT,
    IGNORED_PREFIXES,
    STAGES,
    VERIFICATION_PROOF_VERSION,
    WorkflowStageError,
    _exception_classification,
    concise,
    configured_attempt_limit,
    configured_nonnegative_float,
    issue_number_from_arguments,
    safe_slug,
)

from automation.workflow_storage import (
    _file_sha256,
    _json_evidence,
    read_json,
    read_state,
    read_text,
    write_json,
    write_state,
    write_text,
)

from automation.workflow_commands import (
    _command_failure_classification,
    _command_reason,
    _decoded_text,
    _gh_environment,
    _porcelain_paths,
    _run_captured,
    gh,
    gh_json,
    git,
)

from automation.workflow_workspace import (
    _baseline_snapshot,
    ignored_workspace_path,
    repository_modified,
    source_identity,
    validate_prepared_worktree,
    workspace_changes,
    workspace_file_paths,
    workspace_path_in_scope,
    workspace_snapshot,
    write_workspace_snapshot,
)

from automation.workflow_prompts import (
    commit_message,
    render_ci_repair,
    render_implementer_prompt,
    render_legacy_verifier,
    resolve_profiles,
)

from automation.workflow_diagnostics import (
    _diagnostics,
    _record_shipment_diagnostics,
    _record_stage_invocation,
    _record_stage_timing,
    _repeat_failure_payload,
    _require_accepted_role,
    _stage_input_fingerprint,
    _write_diagnostics,
    record_stage_failure,
    stage_payload,
)

from automation.workflow_github import (
    _ci_state,
    _persist_ci_proof,
    _pr_head_sha,
    _query_pr_checks,
    ensure_pr,
    mark_ready,
    validate_ready_proof,
    wait_for_required_checks,
)

from automation.workflow_preparation import (
    ensure_prepared_issue,
)

from automation.workflow_verification import (
    _preflight,
    pr_and_ci,
    run_local_check,
)

from automation.workflow_dispatch import (
    _execute_stage_impl,
    build_parser,
)

from automation.workflow_dispatch import execute_stage as _base_execute_stage
from automation.workflow_github import create_api_commit as _base_create_api_commit
from automation.workflow_github import mark_blocked as _base_mark_blocked

_original_create_api_commit = _base_create_api_commit
_original_execute_stage = _base_execute_stage
_original_mark_blocked = _base_mark_blocked

def _workspace_snapshot(repo: Path) -> dict[str, str]:
    try:
        return workspace_scope.workspace_snapshot(
            repo,
            fallback_ignored=ignored_workspace_path,
        )
    except workspace_scope.WorkspaceScopeError as exc:
        raise WorkflowStageError(str(exc)) from exc

def _workspace_file_paths(repo: Path) -> list[str]:
    try:
        return workspace_scope.workspace_paths(
            repo,
            fallback_ignored=ignored_workspace_path,
        )
    except workspace_scope.WorkspaceScopeError as exc:
        raise WorkflowStageError(str(exc)) from exc

def _workspace_path_in_scope(repo: Path, relative: str) -> bool:
    try:
        return workspace_scope.path_is_in_scope(
            repo,
            relative,
            fallback_ignored=ignored_workspace_path,
        )
    except workspace_scope.WorkspaceScopeError as exc:
        raise WorkflowStageError(str(exc)) from exc

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

def _create_api_commit(
    repo: Path,
    state: dict[str, object],
    changes: list[dict[str, str]],
    current: Path,
    *,
    runner=subprocess.run,
) -> str:
    scoped = set(_workspace_file_paths(repo))
    baseline_path, _ = _baseline_snapshot(current, state)
    baseline = read_json(baseline_path)
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
        raise WorkflowStageError(
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
    autodev_root: Path = AUTODEV_ROOT,
    attempt: int = 0,
    reason: str = "",
    runner=subprocess.run,
    which=shutil.which,
) -> tuple[int, dict[str, object]]:
    repo = repo.expanduser().resolve()
    if name == "preflight":
        try:
            _semantic_budget.validate_config(
                fixed_default=DEFAULT_MAX_SEMANTIC_REPAIR_ATTEMPTS
            )
        except _semantic_budget.SemanticRepairBudgetError as exc:
            raise WorkflowStageError(str(exc)) from exc

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

    current = repo / CURRENT_DIR
    state = read_state(current)
    try:
        budget = _semantic_budget.resolve_budget(
            repo,
            state,
            attempt=attempt,
            fixed_default=DEFAULT_MAX_SEMANTIC_REPAIR_ATTEMPTS,
            runner=runner,
        )
    except _semantic_budget.SemanticRepairBudgetError as exc:
        raise WorkflowStageError(str(exc)) from exc

    if "fixed_limit_observed" not in budget:
        raw_observed = os.environ.get(_semantic_budget.FIXED_LIMIT_ENV, "").strip()
        try:
            observed = (
                int(raw_observed)
                if raw_observed
                else DEFAULT_MAX_SEMANTIC_REPAIR_ATTEMPTS
            )
        except ValueError as exc:
            raise WorkflowStageError(
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
    issue_text = read_text(current / "issue.md") or str(state.get("IssueText", ""))
    try:
        result = parse_semantic_output(
            read_text(result_path),
            expected_criteria=extract_acceptance_criteria(issue_text) or None,
        )
    except SemanticVerifierError:
        return code, payload

    if payload.get("state") == "BLOCKED" and result.get("verdict") == "repair":
        repair_path = current / "verification-repair.md"
        if workspace_scope.is_git_worktree(repo):
            prepare_semantic_repair_prompt(
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
            write_text(
                repair_path,
                "# Semantic repair\n\n"
                + (repair_brief or "Review the final semantic verification result.")
                + "\n",
            )
        state = read_state(current)
        details = _semantic_budget.failure_details(
            result,
            budget,
            attempt=attempt,
            verification_result=".autodev-run/current/verification-result.json",
            repair_artifact=".autodev-run/current/verification-repair.md",
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

    state = read_state(current)
    _semantic_budget.clear_failure_state(repo, state)
    return code, payload

def _mark_blocked(
    current: Path,
    state: dict[str, object],
    reason: str,
    *,
    runner=subprocess.run,
) -> None:
    details = state.get("LastSemanticFailureDetails", {})
    rich_reason = (
        _semantic_budget.human_failure_summary(details, reason)
        if isinstance(details, dict) and details
        else reason
    )
    _original_mark_blocked(current, state, rich_reason, runner=runner)



workspace_snapshot = _workspace_snapshot
workspace_file_paths = _workspace_file_paths
workspace_path_in_scope = _workspace_path_in_scope
create_api_commit = _create_api_commit
execute_stage = _execute_stage
mark_blocked = _mark_blocked
FAILURE_REPAIR_BUDGET_EXHAUSTED = _semantic_budget.FAILURE_REPAIR_BUDGET_EXHAUSTED

# The pre-refactor module was deliberately monkeypatch-friendly: tests and a few
# extension hooks replace attributes on automation.workflow_stages. Keep that
# public seam without making the responsibility modules depend back on this
# facade. Before a facade entrypoint delegates, matching facade overrides are
# copied into the modules that consume them. Production dependency direction
# therefore remains one-way; this adapter exists only at the compatibility edge.
_WORKFLOW_MODULES = (
    _workflow_contract,
    _workflow_storage,
    _workflow_commands,
    _workflow_workspace,
    _workflow_prompts,
    _workflow_diagnostics,
    _workflow_github,
    _workflow_preparation,
    _workflow_verification,
    _workflow_dispatch,
)


def _sync_compat_overrides() -> None:
    facade = globals()
    for module in _WORKFLOW_MODULES:
        namespace = module.__dict__
        for name in tuple(namespace):
            if name.startswith("__") or name not in facade:
                continue
            namespace[name] = facade[name]


def _compat_entrypoint(target):
    @functools.wraps(target)
    def invoke(*args, **kwargs):
        _sync_compat_overrides()
        return target(*args, **kwargs)

    return invoke


def _install_compat_entrypoints() -> None:
    facade = globals()
    wrapped: set[str] = set()
    for module in _WORKFLOW_MODULES:
        for name in tuple(module.__dict__):
            if name in wrapped or name.startswith("__") or name not in facade:
                continue
            value = facade[name]
            if inspect.isfunction(value) and value.__module__.startswith("automation."):
                facade[name] = _compat_entrypoint(value)
                wrapped.add(name)


_install_compat_entrypoints()

# Explicitly install the cross-cutting compatibility boundaries in the modules
# that own the affected workflow operations. The dependency direction remains
# workflow_dispatch -> verification/github/workspace; the public facade only
# supplies policy wrappers at the edge.
_workflow_github.create_api_commit = create_api_commit
_workflow_verification.create_api_commit = create_api_commit
_workflow_github.mark_blocked = mark_blocked
_workflow_dispatch.mark_blocked = mark_blocked
_semantic_budget._resume_budget = _resume_semantic_budget  # type: ignore[attr-defined]
_semantic_budget.install_run_manifest_hooks()
_windows_workflow_hooks.install(sys.modules[__name__])


def run(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo = Path(args.repo).expanduser().resolve()
    try:
        code, payload = execute_stage(
            args.stage,
            repo,
            arguments=args.arguments,
            autodev_root=Path(args.autodev_root),
            attempt=args.attempt,
            reason=args.reason,
        )
    except (WorkflowStageError, SemanticVerifierError, OSError, ValueError) as exc:
        payload = record_stage_failure(
            repo,
            args.stage,
            exc,
            requested_issue=issue_number_from_arguments(args.arguments),
        )
        print(json.dumps(payload, sort_keys=True))
        return 1
    print(json.dumps(payload, sort_keys=True))
    return code


def main() -> int:
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
