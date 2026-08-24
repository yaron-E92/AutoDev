from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable, Mapping
from automation import (
    opencode_adapter,
    opencode_runtime,
    role_resume,
    role_runtime,
    role_runtime_diagnostics,
    workflow_stages,
)
from automation import coordination_contract, coordination_state

from automation.role_coordinator_contract import (
    MAX_TRANSITIONS,
    REPAIR_KINDS,
    ROLE_ACTIONS,
    RoleCoordinatorError,
    RoleResumeErrorAlias,
)
from automation.role_coordinator_runtime import (
    run_role,
)
from automation.role_coordinator_stages import (
    _resume_payload,
    run_stage,
    terminal_payload,
)

def coordinate(
    repo: Path,
    *,
    arguments: str = "",
    resume: bool = False,
    invalidated_roles: set[str] | None = None,
    runtime_name: str = "",
    runtime_registry: Mapping[str, role_runtime.RuntimeFactory] | None = None,
    runner: Callable[..., object] = subprocess.run,
    which=None,
) -> dict[str, object]:
    repo = repo.expanduser().resolve()
    opencode_runtime.install_workflow_guards()
    runtime, runtime_source = role_runtime.select_runtime(
        repo,
        requested=runtime_name,
        registry=runtime_registry,
    )
    try:
        snapshots = runtime.role_snapshots(repo, runner=runner, which=which)
    except role_runtime.RoleRuntimeError as exc:
        raise RoleCoordinatorError(
            str(exc),
            classification=exc.classification,
        ) from exc

    if resume:
        try:
            cursor = _resume_payload(
                repo,
                snapshots,
                invalidated_roles=invalidated_roles,
                runner=runner,
            )
            role_runtime.persist_selection(
                repo,
                name=runtime.name,
                source=runtime_source,
                force_manifest=True,
            )
        except RoleResumeErrorAlias as exc:
            return terminal_payload(
                repo,
                {
                    "state": "FAILED",
                    "reason": str(exc),
                    "failed_stage": "resume",
                    "failure_classification": workflow_stages.FAILURE_DETERMINISTIC,
                },
                arguments=arguments,
            )
    else:
        preflight = run_stage(
            repo,
            "preflight",
            runtime_name=runtime.name,
            arguments=arguments,
            runner=runner,
            which=which,
        )
        if preflight.get("state") != "CONTINUE":
            return terminal_payload(repo, preflight, arguments=arguments)
        prepared = run_stage(
            repo,
            "prepare",
            runtime_name=runtime.name,
            arguments=arguments,
            runner=runner,
            which=which,
        )
        if prepared.get("state") != "CONTINUE":
            return terminal_payload(repo, prepared, arguments=arguments)
        role_runtime.persist_selection(
            repo,
            name=runtime.name,
            source=runtime_source,
            force_manifest=True,
        )
        cursor = _resume_payload(repo, snapshots, runner=runner)

    for _ in range(MAX_TRANSITIONS):
        if cursor.get("state") == "COMPLETE" or cursor.get("next_action") == "complete":
            result = workflow_stages.stage_payload(
                repo,
                "PR_READY",
                "ReadyForReview",
                requested_issue=int(cursor.get("issue_number", 0) or 0),
                next_action="human review",
            )
            result["pr_url"] = str(cursor.get("pr_url", ""))
            result["role_runtime"] = runtime.name
            return result

        action = str(cursor.get("next_action", ""))
        if action in ROLE_ACTIONS:
            run_role(
                repo,
                action,
                runtime,
                snapshots,
                runner=runner,
                which=which,
            )
        elif action == "implementer":
            rendered = run_stage(
                repo,
                "render-implementer",
                runtime_name=runtime.name,
                runner=runner,
                which=which,
            )
            if rendered.get("state") != "CONTINUE":
                return terminal_payload(repo, rendered, arguments=arguments)
            run_role(
                repo,
                "implementer",
                runtime,
                snapshots,
                already_prepared=True,
                runner=runner,
                which=which,
            )
        elif action == "local-check":
            outcome = run_stage(
                repo,
                "local-check",
                runtime_name=runtime.name,
                attempt=int(cursor.get("local_repair_attempt", 0) or 0),
                runner=runner,
                which=which,
            )
            if outcome.get("state") in {"BLOCKED", "FAILED"}:
                return terminal_payload(repo, outcome, arguments=arguments)
        elif action == "verifier":
            run_role(
                repo,
                "verifier",
                runtime,
                snapshots,
                runner=runner,
                which=which,
            )
            outcome = run_stage(
                repo,
                "semantic",
                runtime_name=runtime.name,
                attempt=int(cursor.get("semantic_repair_attempt", 0) or 0),
                runner=runner,
                which=which,
            )
            if outcome.get("state") in {"BLOCKED", "FAILED"}:
                return terminal_payload(repo, outcome, arguments=arguments)
        elif action == "pr-and-ci":
            outcome = run_stage(
                repo,
                "pr-and-ci",
                runtime_name=runtime.name,
                attempt=int(cursor.get("ci_repair_attempt", 0) or 0),
                runner=runner,
                which=which,
            )
            if outcome.get("state") in {"BLOCKED", "FAILED"}:
                return terminal_payload(repo, outcome, arguments=arguments)
        elif action == "ready":
            return terminal_payload(
                repo,
                run_stage(
                    repo,
                    "ready",
                    runtime_name=runtime.name,
                    runner=runner,
                    which=which,
                ),
                arguments=arguments,
            )
        elif action == "prepare":
            outcome = run_stage(
                repo,
                "prepare",
                runtime_name=runtime.name,
                arguments=str(cursor.get("issue_number", "")),
                runner=runner,
                which=which,
            )
            if outcome.get("state") != "CONTINUE":
                return terminal_payload(repo, outcome, arguments=arguments)
        elif action in REPAIR_KINDS:
            run_role(
                repo,
                "fixer",
                runtime,
                snapshots,
                repair_kind=REPAIR_KINDS[action],
                runner=runner,
                which=which,
            )
        else:
            return terminal_payload(
                repo,
                {
                    "state": "FAILED",
                    "reason": f"unsupported deterministic continuation action: {action or '(empty)'}",
                    "failed_stage": str(cursor.get("next_stage", "python-coordinator")),
                    "failure_classification": workflow_stages.FAILURE_DETERMINISTIC,
                },
                arguments=arguments,
            )

        try:
            cursor = _resume_payload(repo, snapshots, runner=runner)
        except role_resume.RoleResumeError as exc:
            return terminal_payload(
                repo,
                {
                    "state": "FAILED",
                    "reason": str(exc),
                    "failed_stage": action or "resume",
                    "failure_classification": workflow_stages.FAILURE_DETERMINISTIC,
                },
                arguments=arguments,
            )

    return terminal_payload(
        repo,
        {
            "state": "FAILED",
            "reason": f"Python coordinator exceeded {MAX_TRANSITIONS} deterministic transitions",
            "failed_stage": "python-coordinator",
            "failure_classification": workflow_stages.FAILURE_DETERMINISTIC,
        },
        arguments=arguments,
    )
