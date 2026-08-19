from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import subprocess
import time
from pathlib import Path
from typing import Callable

from automation import (
    opencode_adapter,
    opencode_cli,
    opencode_resume,
    opencode_runtime,
    privacy,
    role_runtime_diagnostics,
    workflow_stages,
)


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
ROLE_TIMEOUT_ENV = "AUTODEV_OPENCODE_ROLE_TIMEOUT_SECONDS"
MAX_TRANSITIONS = 100


class OpenCodeCoordinatorError(RuntimeError):
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
    specific_name = f"AUTODEV_OPENCODE_{role.upper()}_TIMEOUT_SECONDS"
    raw = os.environ.get(specific_name) or os.environ.get(ROLE_TIMEOUT_ENV)
    if raw:
        try:
            value = int(raw)
        except ValueError as exc:
            raise OpenCodeCoordinatorError(
                f"{specific_name if os.environ.get(specific_name) else ROLE_TIMEOUT_ENV} must be a positive integer"
            ) from exc
        if value <= 0:
            raise OpenCodeCoordinatorError(
                f"{specific_name if os.environ.get(specific_name) else ROLE_TIMEOUT_ENV} must be a positive integer"
            )
        return value
    return ROLE_TIMEOUT_SECONDS.get(role, 900)


def _issue_number(repo: Path, arguments: str = "") -> int:
    try:
        state = workflow_stages.read_state(repo / workflow_stages.CURRENT_DIR)
    except (OSError, ValueError, workflow_stages.WorkflowStageError):
        state = {}
    return int(state.get("IssueNumber", 0) or workflow_stages.issue_number_from_arguments(arguments) or 0)


def role_acceptance(repo: Path, role: str) -> dict[str, object]:
    current = repo / workflow_stages.CURRENT_DIR
    try:
        state = workflow_stages.read_state(current)
    except (OSError, ValueError, workflow_stages.WorkflowStageError) as exc:
        return {"state": "MISSING", "role": role, "reason": f"cannot read durable role state: {exc}"}

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
    return {"state": "ACCEPTED", "role": role, "artifact": artifact, "sha256": expected}


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


def _record_runtime_failure(
    repo: Path,
    role: str,
    *,
    phase: str,
    repair_kind: str,
    returncode: int | None,
    elapsed_ms: int,
    stdout: object = "",
    stderr: object = "",
    termination: str,
    classification: str,
    reason: str,
) -> str:
    return role_runtime_diagnostics.record_attempt(
        repo,
        role=role,
        phase=phase,
        runtime="opencode",
        output_path=_role_output_path(repo, role),
        returncode=returncode,
        elapsed_ms=elapsed_ms,
        stdout=stdout,
        stderr=stderr,
        accepted=False,
        failure_classification=classification,
        failure_reason=reason,
        termination=termination,
    )


def _run_agent_process(
    repo: Path,
    role: str,
    prompt: str,
    *,
    runner: Callable[..., object],
    which=None,
    repair_kind: str = "",
    phase: str = "work",
) -> dict[str, object]:
    try:
        executable = opencode_cli.resolve_opencode_cli(which=which)
    except opencode_cli.OpenCodeCliError as exc:
        raise OpenCodeCoordinatorError(str(exc)) from exc

    privacy_env = dict(os.environ)
    try:
        policy = privacy.load_policy(repo)
        if policy.enabled:
            mappings = opencode_adapter.resolve_opencode_model_mappings(repo, runner=runner, which=which)
            model = str(mappings.get(role, {}).get("model", "")).strip()
            if not model:
                raise privacy.PrivacyError(
                    f"cannot resolve the effective OpenCode model for AutoDev role {role}; privacy cannot be verified"
                )
            decision, privacy_env = privacy.authorize_opencode_role(
                repo,
                role=role,
                model=model,
                opencode_cli=executable,
                runner=runner,
                base_env=privacy_env,
            )
            print(
                json.dumps(
                    {"event": "privacy", **decision.safe_metadata()},
                    sort_keys=True,
                ),
                flush=True,
            )
    except privacy.PrivacyError as exc:
        raise OpenCodeCoordinatorError(str(exc), classification=exc.classification) from exc

    timeout_seconds = role_timeout_seconds(role)
    command = [
        executable,
        "run",
        "--agent",
        f"autodev-{role}",
        "--dir",
        str(repo),
        "--format",
        "json",
        prompt,
    ]
    event = {
        "event": "role-started",
        "role": role,
        "phase": phase,
        "timeout_seconds": timeout_seconds,
    }
    if repair_kind:
        event["repair_kind"] = repair_kind
    print(json.dumps(event, sort_keys=True), flush=True)
    started = time.monotonic()
    try:
        completed = runner(
            command,
            cwd=repo,
            env=privacy_env,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        print(
            json.dumps(
                {
                    "event": "role-timeout",
                    "role": role,
                    "phase": phase,
                    "repair_kind": repair_kind,
                    "timeout_seconds": timeout_seconds,
                    "elapsed_ms": elapsed_ms,
                },
                sort_keys=True,
            ),
            flush=True,
        )
        reason = f"OpenCode role {role} exceeded configured timeout of {timeout_seconds} seconds"
        diagnostic = _record_runtime_failure(
            repo,
            role,
            phase=phase,
            repair_kind=repair_kind,
            returncode=None,
            elapsed_ms=elapsed_ms,
            stdout=getattr(exc, "stdout", "") or getattr(exc, "output", "") or "",
            stderr=getattr(exc, "stderr", "") or "",
            termination="runtime-timeout",
            classification=workflow_stages.FAILURE_TRANSIENT,
            reason=reason,
        )
        raise OpenCodeCoordinatorError(
            f"{reason}; diagnostic: {diagnostic}",
            classification=workflow_stages.FAILURE_TRANSIENT,
            diagnostic_path=diagnostic,
        ) from exc
    except OSError as exc:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        reason = f"could not launch OpenCode role {role} via {executable!r}: {exc}"
        diagnostic = _record_runtime_failure(
            repo,
            role,
            phase=phase,
            repair_kind=repair_kind,
            returncode=None,
            elapsed_ms=elapsed_ms,
            stderr=str(exc),
            termination="runtime-launch-failed",
            classification=workflow_stages.FAILURE_TRANSIENT,
            reason=reason,
        )
        raise OpenCodeCoordinatorError(
            f"{reason}; diagnostic: {diagnostic}",
            classification=workflow_stages.FAILURE_TRANSIENT,
            diagnostic_path=diagnostic,
        ) from exc

    elapsed_ms = int((time.monotonic() - started) * 1000)
    returncode = int(getattr(completed, "returncode", 1))
    stderr = str(getattr(completed, "stderr", "") or "")
    stdout = str(getattr(completed, "stdout", "") or "")
    if returncode != 0:
        detail = (stderr.strip() or stdout.strip())[-2000:]
        reason = f"OpenCode role {role} exited with code {returncode}" + (f": {detail}" if detail else "")
        diagnostic = _record_runtime_failure(
            repo,
            role,
            phase=phase,
            repair_kind=repair_kind,
            returncode=returncode,
            elapsed_ms=elapsed_ms,
            stdout=stdout,
            stderr=stderr,
            termination="runtime-nonzero",
            classification=workflow_stages.FAILURE_TRANSIENT,
            reason=reason,
        )
        raise OpenCodeCoordinatorError(
            f"{reason}; diagnostic: {diagnostic}",
            classification=workflow_stages.FAILURE_TRANSIENT,
            diagnostic_path=diagnostic,
        )

    finished = {
        "event": "role-finished",
        "role": role,
        "phase": phase,
        "returncode": returncode,
        "elapsed_ms": elapsed_ms,
    }
    if repair_kind:
        finished["repair_kind"] = repair_kind
    print(json.dumps(finished, sort_keys=True), flush=True)
    return {
        "runtime": "opencode",
        "role": role,
        "phase": phase,
        "returncode": returncode,
        "elapsed_ms": elapsed_ms,
        "stdout": stdout,
        "stderr": stderr,
        "termination": "completed",
    }


def _record_validated_attempt(
    repo: Path,
    role: str,
    process: dict[str, object],
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
        phase=str(process.get("phase", "work")),
        runtime=str(process.get("runtime", "opencode")),
        output_path=output,
        returncode=int(process.get("returncode", 0) or 0),
        elapsed_ms=int(process.get("elapsed_ms", 0) or 0),
        stdout=process.get("stdout", ""),
        stderr=process.get("stderr", ""),
        accepted=accepted,
        validation_error=validation_error,
        failure_classification=classification,
        failure_reason=reason,
        termination=str(process.get("termination", "completed")),
    )


def run_role(
    repo: Path,
    role: str,
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
    initial = _run_agent_process(
        repo,
        role,
        prompt,
        runner=runner,
        which=which,
        repair_kind=repair_kind,
    )

    output = _role_output_path(repo, role)
    last_diagnostic = ""
    try:
        opencode_adapter.accept_role(role, repo, output)
    except opencode_adapter.OpenCodeAdapterError as first_error:
        reason = f"OpenCode role {role} output was rejected: {first_error}"
        last_diagnostic = _record_validated_attempt(
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
            raise OpenCodeCoordinatorError(
                f"{reason}; diagnostic: {last_diagnostic}",
                classification=role_runtime_diagnostics.FAILURE_ROLE_PROTOCOL,
                diagnostic_path=last_diagnostic,
            ) from first_error
        correction_process = _run_agent_process(
            repo,
            role,
            CORRECTION_PROMPT.format(role=role),
            runner=runner,
            which=which,
            repair_kind=repair_kind,
            phase="correction",
        )
        try:
            opencode_adapter.accept_role(role, repo, output)
        except opencode_adapter.OpenCodeAdapterError as second_error:
            reason = f"OpenCode role {role} protocol correction failed: {second_error}"
            last_diagnostic = _record_validated_attempt(
                repo,
                role,
                correction_process,
                output,
                accepted=False,
                validation_error=str(second_error),
                classification=role_runtime_diagnostics.FAILURE_ROLE_PROTOCOL_EXHAUSTED,
                reason=reason,
            )
            raise OpenCodeCoordinatorError(
                f"{reason}; diagnostic: {last_diagnostic}",
                classification=role_runtime_diagnostics.FAILURE_ROLE_PROTOCOL_EXHAUSTED,
                diagnostic_path=last_diagnostic,
            ) from second_error
        last_diagnostic = _record_validated_attempt(
            repo,
            role,
            correction_process,
            output,
            accepted=True,
        )
    else:
        last_diagnostic = _record_validated_attempt(
            repo,
            role,
            initial,
            output,
            accepted=True,
        )

    acceptance = role_acceptance(repo, role)
    if acceptance.get("state") != "ACCEPTED":
        reason = (
            f"OpenCode role {role} was not durably accepted after Python validation: "
            f"{acceptance.get('state')} — {acceptance.get('reason', '')}"
        )
        raise OpenCodeCoordinatorError(
            f"{reason}; last role attempt: {last_diagnostic}",
            classification=role_runtime_diagnostics.FAILURE_ROLE_PROTOCOL,
            diagnostic_path=last_diagnostic,
        )
    print(json.dumps({"event": "role-accepted", **acceptance}, sort_keys=True), flush=True)
    return acceptance


def run_stage(
    repo: Path,
    name: str,
    *,
    arguments: str = "",
    attempt: int = 0,
    reason: str = "",
) -> dict[str, object]:
    code, payload = opencode_adapter.workflow_stage(
        name,
        repo,
        arguments=arguments,
        attempt=attempt,
        reason=reason,
    )
    print(json.dumps({"event": "stage", **payload}, sort_keys=True), flush=True)
    if code != 0 and payload.get("state") not in {"FAILED", "BLOCKED", "REPAIR"}:
        raise OpenCodeCoordinatorError(
            str(payload.get("reason", "")) or f"AutoDev stage {name} failed with exit code {code}"
        )
    return payload


def terminal_payload(repo: Path, payload: dict[str, object], *, arguments: str = "") -> dict[str, object]:
    state = str(payload.get("state", "FAILED"))
    if state == "PR_READY":
        return dict(payload)

    current = repo / workflow_stages.CURRENT_DIR
    reason = str(payload.get("reason", "AutoDev workflow stopped"))
    issue = int(payload.get("issue_number", 0) or _issue_number(repo, arguments))
    if state == "BLOCKED":
        try:
            workflow_stages.mark_blocked(current, workflow_stages.read_state(current), reason)
        except (OSError, ValueError, workflow_stages.WorkflowStageError):
            pass
        if opencode_resume.has_manifest(repo):
            opencode_resume.checkpoint_failure(
                repo,
                str(payload.get("failed_stage", "blocked")),
                OpenCodeCoordinatorError(
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

    failure = OpenCodeCoordinatorError(
        reason,
        classification=str(
            payload.get("failure_classification", "") or workflow_stages.FAILURE_DETERMINISTIC
        ),
        diagnostic_path=str(payload.get("artifact", "")),
    )
    if opencode_resume.has_manifest(repo):
        opencode_resume.checkpoint_failure(
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
        next_action="inspect the reported failure, correct it, then run /autodev-resume",
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
    mappings: dict[str, dict[str, str]],
    *,
    invalidated_roles: set[str] | None = None,
) -> dict[str, object]:
    return opencode_resume.resume(repo, mappings, invalidated_roles=invalidated_roles or set())


def coordinate(
    repo: Path,
    *,
    arguments: str = "",
    resume: bool = False,
    invalidated_roles: set[str] | None = None,
    runner: Callable[..., object] = subprocess.run,
    which=None,
) -> dict[str, object]:
    repo = repo.expanduser().resolve()
    opencode_runtime.install_workflow_guards()
    mappings = opencode_adapter.resolve_opencode_model_mappings(repo, runner=runner, which=which)

    if resume:
        try:
            cursor = _resume_payload(repo, mappings, invalidated_roles=invalidated_roles)
        except (opencode_resume.OpenCodeResumeError, opencode_adapter.OpenCodeAdapterError) as exc:
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
        preflight = run_stage(repo, "preflight", arguments=arguments)
        if preflight.get("state") != "CONTINUE":
            return terminal_payload(repo, preflight, arguments=arguments)
        prepared = run_stage(repo, "prepare", arguments=arguments)
        if prepared.get("state") != "CONTINUE":
            return terminal_payload(repo, prepared, arguments=arguments)
        cursor = _resume_payload(repo, mappings)

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
            return result

        action = str(cursor.get("next_action", ""))
        if action in ROLE_ACTIONS:
            run_role(repo, action, runner=runner, which=which)
        elif action == "implementer":
            rendered = run_stage(repo, "render-implementer")
            if rendered.get("state") != "CONTINUE":
                return terminal_payload(repo, rendered, arguments=arguments)
            run_role(repo, "implementer", already_prepared=True, runner=runner, which=which)
        elif action == "local-check":
            outcome = run_stage(
                repo,
                "local-check",
                attempt=int(cursor.get("local_repair_attempt", 0) or 0),
            )
            if outcome.get("state") in {"BLOCKED", "FAILED"}:
                return terminal_payload(repo, outcome, arguments=arguments)
        elif action == "verifier":
            run_role(repo, "verifier", runner=runner, which=which)
            outcome = run_stage(
                repo,
                "semantic",
                attempt=int(cursor.get("semantic_repair_attempt", 0) or 0),
            )
            if outcome.get("state") in {"BLOCKED", "FAILED"}:
                return terminal_payload(repo, outcome, arguments=arguments)
        elif action == "pr-and-ci":
            outcome = run_stage(
                repo,
                "pr-and-ci",
                attempt=int(cursor.get("ci_repair_attempt", 0) or 0),
            )
            if outcome.get("state") in {"BLOCKED", "FAILED"}:
                return terminal_payload(repo, outcome, arguments=arguments)
        elif action == "ready":
            return terminal_payload(repo, run_stage(repo, "ready"), arguments=arguments)
        elif action == "prepare":
            outcome = run_stage(repo, "prepare", arguments=str(cursor.get("issue_number", "")))
            if outcome.get("state") != "CONTINUE":
                return terminal_payload(repo, outcome, arguments=arguments)
        elif action in REPAIR_KINDS:
            run_role(
                repo,
                "fixer",
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
            cursor = _resume_payload(repo, mappings)
        except (opencode_resume.OpenCodeResumeError, opencode_adapter.OpenCodeAdapterError) as exc:
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
            raise OpenCodeCoordinatorError("--invalidate-role must be followed by a valid AutoDev role")
        values.add(tokens[index + 1])
    return values


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="autodev coordinate")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--arguments", default="")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args(argv)
    try:
        payload = coordinate(
            Path(args.repo),
            arguments=args.arguments,
            resume=args.resume,
            invalidated_roles=invalidations(args.arguments) if args.resume else set(),
        )
    except (
        OpenCodeCoordinatorError,
        opencode_adapter.OpenCodeAdapterError,
        opencode_resume.OpenCodeResumeError,
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
                    getattr(exc, "classification", "") or workflow_stages.FAILURE_DETERMINISTIC
                ),
                "artifact": diagnostic_path,
            },
            arguments=args.arguments,
        )
    print(json.dumps(payload, sort_keys=True), flush=True)
    return 0 if payload.get("state") in {"PR_READY", "BLOCKED"} else 1


def main() -> int:
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
