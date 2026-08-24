from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from automation import repair_budget_contract as _budget_contract
from automation import repair_budget_failure as _budget_failure
from automation import repair_budget_policy as _budget_policy
from automation import repair_budget_storage as _budget_storage
from automation import workspace_scope
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
from automation.semantic_contract import SemanticVerifierError
from automation.semantic_invocation import prepare_semantic_repair_prompt
from automation.semantic_prompts import extract_acceptance_criteria
from automation.semantic_schema import parse_semantic_output
from automation.semantic_text import render_template

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
            _budget_policy.validate_config(
                fixed_default=DEFAULT_MAX_SEMANTIC_REPAIR_ATTEMPTS
            )
        except _budget_contract.SemanticRepairBudgetError as exc:
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
        budget = _budget_policy.resolve_budget(
            repo,
            state,
            attempt=attempt,
            fixed_default=DEFAULT_MAX_SEMANTIC_REPAIR_ATTEMPTS,
            runner=runner,
        )
    except _budget_contract.SemanticRepairBudgetError as exc:
        raise WorkflowStageError(str(exc)) from exc

    if "fixed_limit_observed" not in budget:
        raw_observed = os.environ.get(_budget_contract.FIXED_LIMIT_ENV, "").strip()
        try:
            observed = (
                int(raw_observed)
                if raw_observed
                else DEFAULT_MAX_SEMANTIC_REPAIR_ATTEMPTS
            )
        except ValueError as exc:
            raise WorkflowStageError(
                f"{_budget_contract.FIXED_LIMIT_ENV} must be an integer"
            ) from exc
        budget["fixed_limit_observed"] = observed
    _budget_storage.persist_budget(repo, state, budget)

    previous_limit = os.environ.get(_budget_contract.FIXED_LIMIT_ENV)
    os.environ[_budget_contract.FIXED_LIMIT_ENV] = str(
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
            os.environ.pop(_budget_contract.FIXED_LIMIT_ENV, None)
        else:
            os.environ[_budget_contract.FIXED_LIMIT_ENV] = previous_limit

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
        details = _budget_failure.failure_details(
            result,
            budget,
            attempt=attempt,
            verification_result=".autodev-run/current/verification-result.json",
            repair_artifact=".autodev-run/current/verification-repair.md",
            verified_source_identity=str(state.get("VerifiedSourceIdentity", "")),
        )
        payload.update(
            {
                "reason": _budget_failure.concise_failure_reason(details),
                "failure_classification": _budget_contract.FAILURE_REPAIR_BUDGET_EXHAUSTED,
                "root_failure_classification": _budget_contract.ROOT_FAILURE_CLASSIFICATION,
                "failure_fingerprint": str(details.get("failure_fingerprint", "")),
                "repair_brief": str(details.get("repair_brief", "")),
                "semantic_requirements": details.get("requirements", []),
                "semantic_findings": details.get("findings", []),
                "verification_result": str(details.get("verification_result", "")),
                "repair_artifact": str(details.get("repair_artifact", "")),
                "verified_source_identity": str(details.get("verified_source_identity", "")),
            }
        )
        _budget_storage.persist_failure(repo, state, details)
        return code, payload

    state = read_state(current)
    _budget_storage.clear_failure_state(repo, state)
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
        _budget_failure.human_failure_summary(details, reason)
        if isinstance(details, dict) and details
        else reason
    )
    _original_mark_blocked(current, state, rich_reason, runner=runner)



workspace_snapshot = _workspace_snapshot
workspace_file_paths = _workspace_file_paths
workspace_path_in_scope = _workspace_path_in_scope
create_api_commit = _create_api_commit
_WORKFLOW_EXECUTOR = _execute_stage
_POLICY_HOOKS_INSTALLED = False


def _ensure_policy_hooks() -> None:
    global _POLICY_HOOKS_INSTALLED, _WORKFLOW_EXECUTOR
    if _POLICY_HOOKS_INSTALLED:
        return
    from automation import repair_budget_manifest
    from automation import windows_workflow_hooks

    repair_budget_manifest.install_run_manifest_hooks()
    _WORKFLOW_EXECUTOR = windows_workflow_hooks.build_execute_stage(
        sys.modules[__name__],
        _execute_stage,
    )
    _POLICY_HOOKS_INSTALLED = True


def execute_stage(
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
    _ensure_policy_hooks()
    return _WORKFLOW_EXECUTOR(
        name,
        repo,
        arguments=arguments,
        autodev_root=autodev_root,
        attempt=attempt,
        reason=reason,
        runner=runner,
        which=which,
    )


mark_blocked = _mark_blocked
FAILURE_REPAIR_BUDGET_EXHAUSTED = _budget_contract.FAILURE_REPAIR_BUDGET_EXHAUSTED

# Explicitly install the cross-cutting compatibility boundaries in the modules
# that own the affected workflow operations. The dependency direction remains
# workflow_dispatch -> verification/github/workspace; the public facade only
# supplies policy wrappers at the edge.
_workflow_github.create_api_commit = create_api_commit
_workflow_verification.create_api_commit = create_api_commit
_workflow_github.mark_blocked = mark_blocked
_workflow_dispatch.mark_blocked = mark_blocked


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
