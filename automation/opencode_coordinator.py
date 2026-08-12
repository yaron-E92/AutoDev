from __future__ import annotations

import argparse
import hashlib
import json
import shlex
import subprocess
from pathlib import Path
from typing import Callable

from automation import opencode_adapter, opencode_cli, opencode_resume, opencode_runtime, workflow_stages


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
MAX_TRANSITIONS = 100


class OpenCodeCoordinatorError(RuntimeError):
    def __init__(self, message: str, *, classification: str = workflow_stages.FAILURE_DETERMINISTIC) -> None:
        super().__init__(message)
        self.classification = classification


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


def _run_agent_process(
    repo: Path,
    role: str,
    prompt: str,
    *,
    runner: Callable[..., object],
    which=None,
) -> None:
    try:
        executable = opencode_cli.resolve_opencode_cli(which=which)
    except opencode_cli.OpenCodeCliError as exc:
        raise OpenCodeCoordinatorError(str(exc)) from exc

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
    try:
        completed = runner(
            command,
            cwd=repo,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        raise OpenCodeCoordinatorError(
            f"could not launch OpenCode role {role} via {executable!r}: {exc}",
            classification=workflow_stages.FAILURE_TRANSIENT,
        ) from exc

    returncode = int(getattr(completed, "returncode", 1))
    if returncode != 0:
        stderr = str(getattr(completed, "stderr", "") or "").strip()
        stdout = str(getattr(completed, "stdout", "") or "").strip()
        detail = (stderr or stdout)[-2000:]
        raise OpenCodeCoordinatorError(
            f"OpenCode role {role} exited with code {returncode}" + (f": {detail}" if detail else ""),
            classification=workflow_stages.FAILURE_TRANSIENT,
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
    _run_agent_process(repo, role, prompt, runner=runner, which=which)

    output = _role_output_path(repo, role)
    try:
        opencode_adapter.accept_role(role, repo, output)
    except opencode_adapter.OpenCodeAdapterError as first_error:
        correction = repo / workflow_stages.CURRENT_DIR / f"contract-correction-{role}.md"
        if not correction.is_file():
            raise OpenCodeCoordinatorError(
                f"OpenCode role {role} output was rejected: {first_error}"
            ) from first_error
        _run_agent_process(
            repo,
            role,
            CORRECTION_PROMPT.format(role=role),
            runner=runner,
            which=which,
        )
        try:
            opencode_adapter.accept_role(role, repo, output)
        except opencode_adapter.OpenCodeAdapterError as second_error:
            raise OpenCodeCoordinatorError(
                f"OpenCode role {role} protocol correction failed: {second_error}"
            ) from second_error

    acceptance = role_acceptance(repo, role)
    if acceptance.get("state") != "ACCEPTED":
        raise OpenCodeCoordinatorError(
            f"OpenCode role {role} was not durably accepted after Python validation: "
            f"{acceptance.get('state')} — {acceptance.get('reason', '')}"
        )
    print(json.dumps({"event": "role-accepted", **acceptance}, sort_keys=True))
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
    print(json.dumps({"event": "stage", **payload}, sort_keys=True))
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
        payload = terminal_payload(
            Path(args.repo).expanduser().resolve(),
            {
                "state": "FAILED",
                "reason": str(exc),
                "failed_stage": "python-coordinator",
                "failure_classification": str(
                    getattr(exc, "classification", "") or workflow_stages.FAILURE_DETERMINISTIC
                ),
            },
            arguments=args.arguments,
        )
    print(json.dumps(payload, sort_keys=True))
    return 0 if payload.get("state") in {"PR_READY", "BLOCKED"} else 1


def main() -> int:
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
