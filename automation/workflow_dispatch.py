from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from automation.semantic_contract import SemanticVerifierError
from automation.semantic_invocation import prepare_semantic_repair_prompt
from automation.semantic_prompts import extract_acceptance_criteria
from automation.semantic_schema import parse_semantic_output
from automation.semantic_text import render_template
from automation.workflow_contract import (
    AUTODEV_ROOT,
    CURRENT_DIR,
    DEFAULT_MAX_REPAIR_ATTEMPTS,
    DEFAULT_MAX_SEMANTIC_REPAIR_ATTEMPTS,
    FAILURE_CODE_REPAIRABLE,
    FAILURE_DETERMINISTIC,
    STAGES,
    WorkflowStageError,
    configured_attempt_limit,
    issue_number_from_arguments,
)
from automation.workflow_diagnostics import (
    _record_stage_invocation,
    _record_stage_timing,
    _repeat_failure_payload,
    _require_accepted_role,
    record_stage_failure,
    stage_payload,
)
from automation.workflow_github import (
    mark_blocked,
    mark_ready,
    validate_ready_proof,
)
from automation.workflow_preparation import (
    ensure_prepared_issue,
)
from automation.workflow_prompts import (
    render_implementer_prompt,
)
from automation.workflow_storage import (
    read_json,
    read_state,
    read_text,
    write_state,
)
from automation.workflow_verification import (
    _preflight,
    pr_and_ci,
    run_local_check,
)
from automation.workflow_workspace import (
    source_identity,
)

def execute_stage(
    name: str,
    repo: Path,
    *,
    arguments: str = "",
    autodev_root: Path = AUTODEV_ROOT,
    attempt: int = 0,
    reason: str = "",
    runner: Callable[..., object] = subprocess.run,
    which: Callable[[str], str | None] = shutil.which,
) -> tuple[int, dict[str, object]]:
    repo = repo.expanduser().resolve()
    started = time.monotonic()
    invocation_recorded = _record_stage_invocation(repo, name)
    try:
        repeated = _repeat_failure_payload(repo, name)
        if repeated is not None:
            return repeated
        code, payload = _execute_stage_impl(
            name,
            repo,
            arguments=arguments,
            autodev_root=autodev_root,
            attempt=attempt,
            reason=reason,
            runner=runner,
            which=which,
        )
        payload["stage_elapsed_ms"] = int((time.monotonic() - started) * 1000)
        return code, payload
    finally:
        if not invocation_recorded:
            _record_stage_invocation(repo, name)
        _record_stage_timing(repo, name, int((time.monotonic() - started) * 1000))

def _execute_stage_impl(
    name: str,
    repo: Path,
    *,
    arguments: str,
    autodev_root: Path,
    attempt: int,
    reason: str,
    runner: Callable[..., object],
    which: Callable[[str], str | None],
) -> tuple[int, dict[str, object]]:
    autodev_root = autodev_root.expanduser().resolve()
    if name not in STAGES:
        raise WorkflowStageError(f"unsupported workflow stage: {name}")
    if attempt < 0:
        raise WorkflowStageError("workflow stage attempt must be zero or greater")

    if name == "preflight":
        _preflight(repo, arguments, which)
        return 0, stage_payload(
            repo,
            "CONTINUE",
            name,
            requested_issue=issue_number_from_arguments(arguments),
            next_action="prepare the requested issue",
        )

    if name == "prepare":
        current = ensure_prepared_issue(
            repo,
            arguments,
            autodev_root=autodev_root,
            runner=runner,
        )
        return 0, stage_payload(
            repo,
            "CONTINUE",
            name,
            next_action="delegate to autodev-reader",
            artifact=current / "state.json",
        )

    current = repo / CURRENT_DIR
    if name == "failed":
        state_value = read_json(current / "state.json")
        state = state_value if isinstance(state_value, dict) else {}
        if state:
            mark_blocked(current, state, reason or "OpenCode coordinator failed.", runner=runner)
        return 0, stage_payload(
            repo,
            "FAILED",
            name,
            reason=reason or "OpenCode coordinator failed",
            failure_classification=FAILURE_DETERMINISTIC,
            next_action="inspect the failure artifacts, correct the setup/provider/subagent failure, then restart intentionally",
        )

    state = read_state(current)

    if name == "render-implementer":
        _require_accepted_role(current, state, "planner", "plan.md")
        render_implementer_prompt(repo, current, state, autodev_root)
        return 0, stage_payload(
            repo,
            "CONTINUE",
            name,
            artifact=current / "implementer.md",
            next_action="delegate to autodev-implementer; implementer.md is already rendered and must not be prepared again",
        )

    if name == "local-check":
        _require_accepted_role(current, state, "implementer", "commit-message.txt")
        max_attempts = configured_attempt_limit(
            "MAX_REPAIR_ATTEMPTS",
            DEFAULT_MAX_REPAIR_ATTEMPTS,
        )
        passed = run_local_check(repo, current, state, autodev_root, runner=runner)
        if passed:
            return 0, stage_payload(
                repo,
                "CONTINUE",
                name,
                next_action="run semantic verification",
                max_repair_attempts=max_attempts,
            )
        if attempt >= max_attempts:
            return 0, stage_payload(
                repo,
                "BLOCKED",
                name,
                reason="deterministic repair-attempt limit exhausted",
                artifact=current / "local-repair.md",
                failure_classification=FAILURE_DETERMINISTIC,
                next_action="mark the run blocked",
                max_repair_attempts=max_attempts,
            )
        return 0, stage_payload(
            repo,
            "REPAIR",
            name,
            reason="deterministic verification failed",
            artifact=current / "local-repair.md",
            failure_classification=FAILURE_CODE_REPAIRABLE,
            next_action="delegate the local repair to autodev-fixer, increment the attempt, then rerun local-check",
            max_repair_attempts=max_attempts,
        )

    if name == "semantic":
        max_attempts = configured_attempt_limit(
            "MAX_SEMANTIC_REPAIR_ATTEMPTS",
            DEFAULT_MAX_SEMANTIC_REPAIR_ATTEMPTS,
        )
        result_path = current / "verification-result.json"
        if not result_path.is_file() or not read_text(result_path).strip():
            raise WorkflowStageError(
                "semantic prerequisite not met: .autodev-run/current/verification-result.json is missing; "
                "run the verifier role and accept its result before the semantic stage"
            )
        _require_accepted_role(current, state, "verifier", "verification-result.json")
        if state.get("VerificationProofVersion"):
            proof = source_identity(repo, current, state)
            if proof["identity"] != str(state.get("VerifiedSourceIdentity", "")):
                raise WorkflowStageError(
                    "semantic prerequisite not met: source changed after deterministic verification; rerun local-check"
                )
            if proof["parent_sha"] != str(state.get("VerifiedParentSha", "")):
                raise WorkflowStageError(
                    "semantic prerequisite not met: source parent changed after deterministic verification; rerun local-check"
                )
        issue_text = read_text(current / "issue.md") or str(state.get("IssueText", ""))
        result = parse_semantic_output(
            read_text(result_path),
            expected_criteria=extract_acceptance_criteria(issue_text) or None,
        )
        verdict = str(result["verdict"])
        state["LastSemanticVerdict"] = verdict
        if verdict == "pass" and state.get("VerificationProofVersion"):
            state["SemanticSourceIdentity"] = str(state.get("VerifiedSourceIdentity", ""))
        else:
            state.pop("SemanticSourceIdentity", None)
        write_state(current, state)
        if verdict == "pass":
            return 0, stage_payload(
                repo,
                "CONTINUE",
                name,
                next_action="run commit/push/PR/CI",
                max_semantic_repair_attempts=max_attempts,
            )
        if verdict == "blocked":
            return 0, stage_payload(
                repo,
                "BLOCKED",
                name,
                reason="semantic verifier blocked the run",
                artifact=result_path,
                failure_classification=FAILURE_DETERMINISTIC,
                next_action="mark the run blocked",
                max_semantic_repair_attempts=max_attempts,
            )
        if attempt >= max_attempts:
            return 0, stage_payload(
                repo,
                "BLOCKED",
                name,
                reason="semantic repair-attempt limit exhausted",
                artifact=result_path,
                failure_classification=FAILURE_DETERMINISTIC,
                next_action="mark the run blocked",
                max_semantic_repair_attempts=max_attempts,
            )
        repair_path = current / "verification-repair.md"
        prepare_semantic_repair_prompt(
            repo,
            current,
            autodev_root / "promptTemplates" / "semantic-repair.md",
            repair_path,
        )
        return 0, stage_payload(
            repo,
            "REPAIR",
            name,
            reason=str(result.get("repair_brief", "semantic repair requested")),
            artifact=repair_path,
            failure_classification=FAILURE_CODE_REPAIRABLE,
            next_action="delegate the semantic repair to autodev-fixer, increment the attempt, rerun local-check, then rerun autodev-verifier",
            max_semantic_repair_attempts=max_attempts,
        )

    if name == "pr-and-ci":
        if state.get("OpenCodeProtocolVersion"):
            if not bool(state.get("LastLocalCheckPassed")):
                raise WorkflowStageError(
                    "pr-and-ci prerequisite not met: deterministic local verification has not passed"
                )
            if str(state.get("LastSemanticVerdict", "")) != "pass":
                raise WorkflowStageError(
                    "pr-and-ci prerequisite not met: semantic verification has not produced an accepted pass verdict"
                )
        max_attempts = configured_attempt_limit(
            "MAX_REPAIR_ATTEMPTS",
            DEFAULT_MAX_REPAIR_ATTEMPTS,
        )
        ci_passed = pr_and_ci(repo, current, state, autodev_root, runner=runner)
        if ci_passed:
            return 0, stage_payload(
                repo,
                "CONTINUE",
                name,
                next_action="mark the PR ready for human review",
                max_repair_attempts=max_attempts,
            )
        if attempt >= max_attempts:
            return 0, stage_payload(
                repo,
                "BLOCKED",
                name,
                reason="CI repair-attempt limit exhausted",
                artifact=current / "ci-repair.md",
                failure_classification=FAILURE_DETERMINISTIC,
                next_action="mark the run blocked",
                max_repair_attempts=max_attempts,
            )
        return 0, stage_payload(
            repo,
            "REPAIR",
            name,
            reason="required PR checks failed",
            artifact=current / "ci-repair.md",
            failure_classification=FAILURE_CODE_REPAIRABLE,
            next_action="delegate the CI repair to autodev-fixer, increment the attempt, rerun local-check and semantic verification, then retry pr-and-ci",
            max_repair_attempts=max_attempts,
        )

    if name == "ready":
        if not str(state.get("PrUrl", "")).strip():
            raise WorkflowStageError("cannot mark ready because state.json has no PR URL")
        validate_ready_proof(current, state, runner=runner)
        mark_ready(current, state, runner=runner)
        return 0, stage_payload(
            repo,
            "PR_READY",
            name,
            next_action="human review; AutoDev never merges automatically",
        )

    if name == "blocked":
        mark_blocked(current, state, reason or "OpenCode coordinator blocked the run.", runner=runner)
        return 0, stage_payload(
            repo,
            "BLOCKED",
            name,
            reason=reason,
            failure_classification=FAILURE_DETERMINISTIC,
            next_action="inspect the current AutoDev artifacts and intervene manually",
        )

    status = str(state.get("Status", ""))
    outcome = "PR_READY" if status == "ReadyForReview" else "BLOCKED" if status == "Blocked" else "CONTINUE"
    return 0, stage_payload(
        repo,
        outcome,
        name,
        next_action="human review" if outcome == "PR_READY" else "continue from the current AutoDev stage",
    )

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Portable AutoDev non-model workflow stages.")
    parser.add_argument("stage", choices=STAGES)
    parser.add_argument("--repo", default=".")
    parser.add_argument("--arguments", default="")
    parser.add_argument("--autodev-root", default=str(AUTODEV_ROOT))
    parser.add_argument("--attempt", type=int, default=0)
    parser.add_argument("--reason", default="")
    return parser

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
