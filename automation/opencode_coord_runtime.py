from __future__ import annotations

import json
import os
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
from automation import coordination_contract, coordination_state

from automation.opencode_coord_contract import (
    CORRECTION_PROMPT,
    OpenCodeCoordinatorError,
    ROLE_PROMPT,
    role_timeout_seconds,
)
from automation.opencode_coord_state import (
    _prepare_role,
    _role_output_path,
    role_acceptance,
)

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
