from __future__ import annotations

from automation import execution_classification_boundary, opencode_adapter_roles

from automation import opencode_adapter_protocol

from automation import opencode_adapter_contract

import json
import subprocess
from pathlib import Path
from typing import Callable, Mapping
from automation import (
    external_error_sanitizer,
    opencode_runtime,
    role_resume,
    role_runtime,
    role_runtime_diagnostics,
    ux_role_context,
    workflow_stages,
)
from automation.planner_output import PlannerOutputError
from automation.semantic_contract import SemanticVerifierError
from automation import coordination_contract, coordination_state

from automation.role_coordinator_contract import (
    CORRECTION_PROMPT,
    ROLE_PROMPT,
    RoleCoordinatorError,
    role_timeout_seconds,
)
from automation.role_coordinator_state import (
    _prepare_role,
    _role_output_path,
    role_acceptance,
)


def _ux_role_prompt(repo: Path, role: str) -> str:
    current = repo / workflow_stages.CURRENT_DIR
    issue_path = current / "issue.md"
    issue_text = issue_path.read_text(encoding="utf-8") if issue_path.is_file() else ""
    try:
        prompt, _ = ux_role_context.prepare_role_context(
            repo,
            current,
            role,
            issue_text,
        )
    except ux_role_context.UXRoleContextError as exc:
        raise RoleCoordinatorError(
            str(exc),
            classification="setup/configuration",
        ) from exc
    return prompt


def _accept_role(
    repo: Path,
    role: str,
    input_path: Path | None,
    snapshots: dict[str, object],
    *,
    runtime_name: str,
) -> list[Path]:
    current = repo / workflow_stages.CURRENT_DIR
    opencode_adapter_protocol._write_role_contracts(current)
    try:
        outputs = opencode_adapter_roles._accept_role_once(role, current, input_path)
    except (
        opencode_adapter_contract.OpenCodeAdapterError,
        PlannerOutputError,
        SemanticVerifierError,
    ) as exc:
        opencode_adapter_roles._raise_contract_rejection(current, role, input_path, exc)
        raise AssertionError("contract rejection must raise") from exc
    opencode_adapter_protocol._mark_role_accepted(current, role, outputs)
    opencode_adapter_protocol._reset_current_correction(current, role)
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
    external_error: external_error_sanitizer.SafeExternalError | None = None,
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
        external_error=external_error,
    )

def _runtime_failure(
    repo: Path,
    role: str,
    result: role_runtime.RoleInvocationResult,
) -> None:
    if result.termination == "runtime-timeout":
        category = "runtime-timeout"
        detail_source = ""
        prefix = (
            f"role runtime {result.runtime} timed out while executing {role} "
            f"after {result.elapsed_ms} ms"
        )
    elif result.termination == "runtime-launch-failed":
        category = "runtime-launch-failed"
        detail_source = result.stderr or result.stdout
        prefix = f"could not launch role runtime {result.runtime} for {role}"
    else:
        category = "runtime-nonzero"
        detail_source = result.stderr or result.stdout
        prefix = (
            f"role runtime {result.runtime} exited with code {result.returncode} for {role}"
        )

    safe_error = external_error_sanitizer.safe_external_error(
        category=category,
        message=detail_source,
        role=role,
        runtime=result.runtime,
        phase=result.phase,
        returncode=result.returncode,
        retry_classification=workflow_stages.FAILURE_TRANSIENT,
        termination=result.termination,
    )
    reason = prefix
    if safe_error.message:
        reason += f": {safe_error.message}"

    diagnostic = _record_attempt(
        repo,
        role,
        result,
        _role_output_path(repo, role),
        accepted=False,
        classification=workflow_stages.FAILURE_TRANSIENT,
        reason=reason,
        external_error=safe_error,
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
        safe_error = external_error_sanitizer.safe_external_error(
            category="runtime-exception",
            message=str(exc),
            role=role,
            runtime=runtime.name,
            phase=phase,
            retry_classification=exc.classification,
            termination="runtime-exception",
        )
        message = f"role runtime {runtime.name} failed while executing {role}"
        if safe_error.message:
            message += f": {safe_error.message}"
        raise RoleCoordinatorError(
            message,
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
    ux_prompt = _ux_role_prompt(repo, role)
    if ux_prompt:
        prompt = prompt.rstrip() + ux_prompt + "\n"
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
    first_reader_text = ""
    first_diagnostic = ""
    last_diagnostic = ""
    try:
        _accept_role(
            repo,
            role,
            output,
            snapshots,
            runtime_name=runtime.name,
        )
    except opencode_adapter_contract.OpenCodeAdapterError as first_error:
        reason = f"role {role} output was rejected: {first_error}"
        first_diagnostic = _record_attempt(
            repo,
            role,
            initial,
            output,
            accepted=False,
            validation_error=str(first_error),
            classification=role_runtime_diagnostics.FAILURE_ROLE_PROTOCOL,
            reason=reason,
        )
        last_diagnostic = first_diagnostic
        if role == "reader" and output is not None:
            try:
                first_reader_text = output.read_text(encoding="utf-8")
            except OSError:
                first_reader_text = ""
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
        deterministic_fallback_applied = False
        try:
            _accept_role(
                repo,
                role,
                output,
                snapshots,
                runtime_name=runtime.name,
            )
        except opencode_adapter_contract.OpenCodeAdapterError as second_error:
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

            fallback = None
            if role == "reader" and output is not None:
                try:
                    fallback = (
                        execution_classification_boundary.prepare_reader_invalid_downgrade_fallback(
                            repo / workflow_stages.CURRENT_DIR,
                            output,
                            first_error,
                            second_error,
                            first_reader_text=first_reader_text,
                        )
                    )
                except (
                    execution_classification_boundary.ExternalBoundaryEvidenceError,
                    OSError,
                    ValueError,
                ):
                    fallback = None

            if fallback is None:
                raise RoleCoordinatorError(
                    f"{reason}; diagnostic: {last_diagnostic}",
                    classification=role_runtime_diagnostics.FAILURE_ROLE_PROTOCOL_EXHAUSTED,
                    diagnostic_path=last_diagnostic,
                ) from second_error

            fallback_report, first_rejection, second_rejection = fallback
            try:
                _accept_role(
                    repo,
                    role,
                    output,
                    snapshots,
                    runtime_name=runtime.name,
                )
            except opencode_adapter_contract.OpenCodeAdapterError as fallback_error:
                fallback_reason = (
                    "reader deterministic downgrade fallback could not be accepted: "
                    f"{fallback_error}"
                )
                raise RoleCoordinatorError(
                    f"{fallback_reason}; diagnostic: {last_diagnostic}",
                    classification=role_runtime_diagnostics.FAILURE_ROLE_PROTOCOL_EXHAUSTED,
                    diagnostic_path=last_diagnostic,
                ) from fallback_error

            fallback_path = (
                execution_classification_boundary.finalize_reader_invalid_downgrade_fallback(
                    repo / workflow_stages.CURRENT_DIR,
                    fallback_report,
                    first_rejection=first_rejection,
                    second_rejection=second_rejection,
                    first_attempt=first_diagnostic,
                    correction_attempt=last_diagnostic,
                )
            )
            deterministic_fallback_applied = True
            print(
                json.dumps(
                    {
                        "event": "reader-execution-classification-fallback",
                        "classification": fallback_report.classification,
                        "source": fallback_report.source,
                        "artifact": str(fallback_path),
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
        if not deterministic_fallback_applied:
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
