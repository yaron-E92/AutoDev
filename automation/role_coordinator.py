from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
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
from automation.prompt_runner import PromptRunnerError
from automation.semantic_verifier import SemanticVerifierError


ROLE_PROMPT = (
    "AutoDev Python has already prepared the current {role} role. "
    "Follow the installed autodev-{role} contract for the model-heavy work only: "
    "read the prepared .autodev-run/current artifacts, perform the requested reasoning or edits, "
    "and write only the contract output artifact. Do not run AutoDev prepare or accept commands; "
    "Python will validate and accept the result after this process exits. "
    "Return only success/failure and the output artifact path."
)
CORRECTION_PROMPT = (
    "AutoDev rejected the current {role} output once. Read "
    ".autodev-run/current/contract-correction-{role}.md, correct only the designated output artifact, "
    "and stop. Do not run AutoDev prepare or accept commands; Python will perform the final validation."
)
REPAIR_KINDS = {"fixer-local": "local", "fixer-semantic": "semantic", "fixer-ci": "ci"}
ROLE_ACTIONS = {"reader", "synthesizer", "planner"}
ROLE_TIMEOUT_SECONDS = {
    "reader": 600,
    "synthesizer": 900,
    "planner": 900,
    "implementer": 1800,
    "fixer": 900,
    "verifier": 900,
}
ROLE_TIMEOUT_ENV = "AUTODEV_ROLE_TIMEOUT_SECONDS"
LEGACY_ROLE_TIMEOUT_ENV = "AUTODEV_OPENCODE_ROLE_TIMEOUT_SECONDS"
MAX_TRANSITIONS = 100


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


def _issue_number(repo: Path, arguments: str = "") -> int:
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


def _role_output_path(repo: Path, role: str) -> Path | None:
    relative = str(opencode_adapter.role_contracts().get(role, {}).get("output_artifact", ""))
    if relative.startswith(".autodev-run/current/"):
        return repo / workflow_stages.CURRENT_DIR / Path(relative).name
    return None


def _prepare_role(repo: Path, role: str, *, repair_kind: str = "") -> None:
    if role == "implementer":
        return
    issue = _issue_number(repo)
    arguments = f"{issue} {repair_kind}".strip() if repair_kind else str(issue)
    opencode_adapter.prepare_role(role, repo, arguments)


def _accept_role(
    repo: Path,
    role: str,
    input_path: Path | None,
    snapshots: dict[str, object],
    *,
    runtime_name: str,
) -> list[Path]:
    current = repo / workflow_stages.CURRENT_DIR
    opencode_adapter._write_role_contracts(current)
    try:
        outputs = opencode_adapter._accept_role_once(role, current, input_path)
    except (
        opencode_adapter.OpenCodeAdapterError,
        PromptRunnerError,
        SemanticVerifierError,
    ) as exc:
        opencode_adapter._raise_contract_rejection(current, role, input_path, exc)
        raise AssertionError("contract rejection must raise") from exc
    opencode_adapter._mark_role_accepted(current, role, outputs)
    opencode_adapter._reset_current_correction(current, role)
    role_resume.checkpoint_role(
        repo,
        role,
        outputs,
        snapshots,
        runtime_name=runtime_name,
    )
    return outputs


def _record_attempt(
    repo: Path,
    role: str,
    result: role_runtime.RoleInvocationResult,
    output: Path | None,
    *,
    accepted: bool,
    validation_error: str = "",
    classification: str = "",
    reason: str = "",
) -> str:
    return role_runtime_diagnostics.record_attempt(
        repo,
        role=role,
        phase=result.phase,
        runtime=result.runtime,
        output_path=output,
        returncode=result.returncode,
        elapsed_ms=result.elapsed_ms,
        stdout=result.stdout,
        stderr=result.stderr,
        accepted=accepted,
        validation_error=validation_error,
        failure_classification=classification,
        failure_reason=reason,
        termination=result.termination,
        model=result.model,
    )


def _runtime_failure(
    repo: Path,
    role: str,
    result: role_runtime.RoleInvocationResult,
) -> None:
    if result.termination == "runtime-timeout":
        reason = (
            f"role runtime {result.runtime} timed out while executing {role} "
            f"after {result.elapsed_ms} ms"
        )
    elif result.termination == "runtime-launch-failed":
        detail = result.stderr.strip()[-1000:]
        reason = f"could not launch role runtime {result.runtime} for {role}"
        if detail:
            reason += f": {detail}"
    else:
        detail = (result.stderr.strip() or result.stdout.strip())[-2000:]
        reason = (
            f"role runtime {result.runtime} exited with code {result.returncode} for {role}"
            + (f": {detail}" if detail else "")
        )
    diagnostic = _record_attempt(
        repo,
        role,
        result,
        _role_output_path(repo, role),
        accepted=False,
        classification=workflow_stages.FAILURE_TRANSIENT,
        reason=reason,
    )
    raise RoleCoordinatorError(
        f"{reason}; diagnostic: {diagnostic}",
        classification=workflow_stages.FAILURE_TRANSIENT,
        diagnostic_path=diagnostic,
    )


def _invoke(
    runtime: role_runtime.RoleRuntime,
    repo: Path,
    role: str,
    prompt: str,
    *,
    phase: str,
    repair_kind: str,
    runner: Callable[..., object],
    which=None,
) -> role_runtime.RoleInvocationResult:
    timeout_seconds = role_timeout_seconds(role)
    event = {
        "event": "role-started",
        "role": role,
        "runtime": runtime.name,
        "phase": phase,
        "timeout_seconds": timeout_seconds,
    }
    if repair_kind:
        event["repair_kind"] = repair_kind
    print(json.dumps(event, sort_keys=True), flush=True)
    try:
        result = runtime.invoke(
            role_runtime.RoleInvocationContext(
                repo=repo,
                role=role,
                prompt=prompt,
                phase=phase,
                repair_kind=repair_kind,
                timeout_seconds=timeout_seconds,
            ),
            runner=runner,
            which=which,
        )
    except role_runtime.RoleRuntimeError as exc:
        raise RoleCoordinatorError(
            str(exc),
            classification=exc.classification,
        ) from exc
    if result.runtime != runtime.name or result.role != role or result.phase != phase:
        raise RoleCoordinatorError(
            "role runtime returned invocation metadata that does not match the requested runtime/role/phase"
        )
    if result.termination != "completed" or result.returncode != 0:
        if result.termination == "runtime-timeout":
            print(
                json.dumps(
                    {
                        "event": "role-timeout",
                        "role": role,
                        "runtime": runtime.name,
                        "phase": phase,
                        "timeout_seconds": timeout_seconds,
                        "elapsed_ms": result.elapsed_ms,
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
        _runtime_failure(repo, role, result)
    finished = {
        "event": "role-finished",
        "role": role,
        "runtime": runtime.name,
        "phase": phase,
        "returncode": result.returncode,
        "elapsed_ms": result.elapsed_ms,
    }
    if repair_kind:
        finished["repair_kind"] = repair_kind
    print(json.dumps(finished, sort_keys=True), flush=True)
    return result


def run_role(
    repo: Path,
    role: str,
    runtime: role_runtime.RoleRuntime,
    snapshots: dict[str, object],
    *,
    repair_kind: str = "",
    already_prepared: bool = False,
    runner: Callable[..., object] = subprocess.run,
    which=None,
) -> dict[str, object]:
    if not already_prepared:
        _prepare_role(repo, role, repair_kind=repair_kind)

    prompt = ROLE_PROMPT.format(role=role)
    if repair_kind:
        prompt += f" The prepared repair kind is {repair_kind}."
    initial = _invoke(
        runtime,
        repo,
        role,
        prompt,
        phase="work",
        repair_kind=repair_kind,
        runner=runner,
        which=which,
    )

    output = _role_output_path(repo, role)
    last_diagnostic = ""
    try:
        _accept_role(
            repo,
            role,
            output,
            snapshots,
            runtime_name=runtime.name,
        )
    except opencode_adapter.OpenCodeAdapterError as first_error:
        reason = f"role {role} output was rejected: {first_error}"
        last_diagnostic = _record_attempt(
            repo,
            role,
            initial,
            output,
            accepted=False,
            validation_error=str(first_error),
            classification=role_runtime_diagnostics.FAILURE_ROLE_PROTOCOL,
            reason=reason,
        )
        correction = repo / workflow_stages.CURRENT_DIR / f"contract-correction-{role}.md"
        if not correction.is_file():
            raise RoleCoordinatorError(
                f"{reason}; diagnostic: {last_diagnostic}",
                classification=role_runtime_diagnostics.FAILURE_ROLE_PROTOCOL,
                diagnostic_path=last_diagnostic,
            ) from first_error

        correction_result = _invoke(
            runtime,
            repo,
            role,
            CORRECTION_PROMPT.format(role=role),
            phase="correction",
            repair_kind=repair_kind,
            runner=runner,
            which=which,
        )
        try:
            _accept_role(
                repo,
                role,
                output,
                snapshots,
                runtime_name=runtime.name,
            )
        except opencode_adapter.OpenCodeAdapterError as second_error:
            reason = f"role {role} protocol correction failed: {second_error}"
            last_diagnostic = _record_attempt(
                repo,
                role,
                correction_result,
                output,
                accepted=False,
                validation_error=str(second_error),
                classification=role_runtime_diagnostics.FAILURE_ROLE_PROTOCOL_EXHAUSTED,
                reason=reason,
            )
            raise RoleCoordinatorError(
                f"{reason}; diagnostic: {last_diagnostic}",
                classification=role_runtime_diagnostics.FAILURE_ROLE_PROTOCOL_EXHAUSTED,
                diagnostic_path=last_diagnostic,
            ) from second_error
        last_diagnostic = _record_attempt(
            repo,
            role,
            correction_result,
            output,
            accepted=True,
        )
    else:
        last_diagnostic = _record_attempt(
            repo,
            role,
            initial,
            output,
            accepted=True,
        )

    acceptance = role_acceptance(repo, role)
    if acceptance.get("state") != "ACCEPTED":
        reason = (
            f"role {role} was not durably accepted after Python validation: "
            f"{acceptance.get('state')} — {acceptance.get('reason', '')}"
        )
        raise RoleCoordinatorError(
            f"{reason}; last role attempt: {last_diagnostic}",
            classification=role_runtime_diagnostics.FAILURE_ROLE_PROTOCOL,
            diagnostic_path=last_diagnostic,
        )
    print(
        json.dumps(
            {"event": "role-accepted", "runtime": runtime.name, **acceptance},
            sort_keys=True,
        ),
        flush=True,
    )
    return acceptance


def run_stage(
    repo: Path,
    name: str,
    *,
    runtime_name: str,
    arguments: str = "",
    attempt: int = 0,
    reason: str = "",
    runner: Callable[..., object] = subprocess.run,
    which=None,
) -> dict[str, object]:
    try:
        code, payload = workflow_stages.execute_stage(
            name,
            repo,
            arguments=arguments,
            attempt=attempt,
            reason=reason,
            runner=runner,
            which=which or workflow_stages.shutil.which,
        )
    except workflow_stages.WorkflowStageError as exc:
        role_resume.checkpoint_failure(repo, name, exc)
        raise RoleCoordinatorError(
            str(exc),
            classification=exc.classification,
        ) from exc

    print(json.dumps({"event": "stage", **payload}, sort_keys=True), flush=True)
    current = repo / workflow_stages.CURRENT_DIR
    if name == "prepare" and payload.get("state") == "CONTINUE" and current.is_dir():
        opencode_adapter._ensure_opencode_protocol(current)
        role_resume.create_manifest(
            repo,
            workflow_stages.read_state(current),
            runtime_name=runtime_name,
        )
        role_runtime.persist_selection(
            repo,
            name=runtime_name,
            source="selected",
            force_manifest=True,
        )
    elif name == "render-implementer" and payload.get("state") == "CONTINUE" and current.is_dir():
        opencode_adapter._ensure_opencode_protocol(current)
        opencode_adapter._begin_role_invocation(current, "implementer")
    if name != "prepare" and role_resume.has_manifest(repo):
        role_resume.checkpoint_stage(repo, name, payload, attempt)
    if code != 0 and payload.get("state") not in {"FAILED", "BLOCKED", "REPAIR"}:
        raise RoleCoordinatorError(
            str(payload.get("reason", ""))
            or f"AutoDev stage {name} failed with exit code {code}"
        )
    return payload


def terminal_payload(
    repo: Path,
    payload: dict[str, object],
    *,
    arguments: str = "",
) -> dict[str, object]:
    state = str(payload.get("state", "FAILED"))
    if state == "PR_READY":
        return dict(payload)
    current = repo / workflow_stages.CURRENT_DIR
    reason = str(payload.get("reason", "AutoDev workflow stopped"))
    issue = int(payload.get("issue_number", 0) or _issue_number(repo, arguments))
    if state == "BLOCKED":
        try:
            workflow_stages.mark_blocked(
                current,
                workflow_stages.read_state(current),
                reason,
            )
        except (OSError, ValueError, workflow_stages.WorkflowStageError):
            pass
        if role_resume.has_manifest(repo):
            role_resume.checkpoint_failure(
                repo,
                str(payload.get("failed_stage", "blocked")),
                RoleCoordinatorError(
                    reason,
                    classification=str(
                        payload.get("failure_classification", "")
                        or workflow_stages.FAILURE_DETERMINISTIC
                    ),
                ),
            )
        result = dict(payload)
        result["state"] = "BLOCKED"
        return result

    failure = RoleCoordinatorError(
        reason,
        classification=str(
            payload.get("failure_classification", "")
            or workflow_stages.FAILURE_DETERMINISTIC
        ),
        diagnostic_path=str(payload.get("artifact", "")),
    )
    if role_resume.has_manifest(repo):
        role_resume.checkpoint_failure(
            repo,
            str(payload.get("failed_stage", "python-coordinator")),
            failure,
        )
    result = workflow_stages.stage_payload(
        repo,
        "FAILED",
        str(payload.get("failed_stage", "python-coordinator")),
        reason=reason,
        requested_issue=issue,
        next_action="inspect the reported failure, correct it, then resume AutoDev",
        failure_classification=failure.classification,
        failure_fingerprint=str(payload.get("failure_fingerprint", "")),
    )
    result["stage"] = "python-coordinator"
    artifact = str(payload.get("artifact", ""))
    if artifact:
        result["artifact"] = artifact
    return result


def _resume_payload(
    repo: Path,
    snapshots: dict[str, object],
    *,
    invalidated_roles: set[str] | None = None,
    runner: Callable[..., object] = subprocess.run,
) -> dict[str, object]:
    return role_resume.resume(
        repo,
        snapshots,
        invalidated_roles=invalidated_roles or set(),
        runner=runner,
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


def invalidations(arguments: str) -> set[str]:
    tokens = shlex.split(arguments or "")
    values: set[str] = set()
    for index, token in enumerate(tokens):
        if token != "--invalidate-role":
            continue
        if index + 1 >= len(tokens) or tokens[index + 1] not in opencode_adapter.ROLE_NAMES:
            raise RoleCoordinatorError(
                "--invalidate-role must be followed by a valid AutoDev role"
            )
        values.add(tokens[index + 1])
    return values


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="autodev coordinate")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--arguments", default="")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--runtime", default="")
    args = parser.parse_args(argv)
    try:
        payload = coordinate(
            Path(args.repo),
            arguments=args.arguments,
            resume=args.resume,
            invalidated_roles=invalidations(args.arguments) if args.resume else set(),
            runtime_name=args.runtime,
        )
    except (
        RoleCoordinatorError,
        role_runtime.RoleRuntimeError,
        role_resume.RoleResumeError,
        opencode_adapter.OpenCodeAdapterError,
        workflow_stages.WorkflowStageError,
        OSError,
        ValueError,
    ) as exc:
        diagnostic_path = str(getattr(exc, "diagnostic_path", "") or "")
        payload = terminal_payload(
            Path(args.repo).expanduser().resolve(),
            {
                "state": "FAILED",
                "reason": str(exc),
                "failed_stage": "python-coordinator",
                "failure_classification": str(
                    getattr(exc, "classification", "")
                    or workflow_stages.FAILURE_DETERMINISTIC
                ),
                "artifact": diagnostic_path,
            },
            arguments=args.arguments,
        )
    print(json.dumps(payload, sort_keys=True), flush=True)
    return 0 if payload.get("state") in {"PR_READY", "BLOCKED"} else 1


def main() -> int:
    return run()


# Kept as a local alias only so the resume branch above can stay explicit about
# the legacy error boundary while this module owns the generic coordinator.
RoleResumeErrorAlias = role_resume.RoleResumeError


if __name__ == "__main__":
    raise SystemExit(main())
