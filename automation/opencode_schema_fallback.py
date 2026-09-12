from __future__ import annotations

from automation import role_output_contract, role_runtime


SCHEMA_FALLBACK_STATE = "native-schema-exhausted->fallback-text"


def prompt(
    source_prompt: str,
    contract: role_output_contract.RoleOutputContract,
) -> str:
    if contract.role == "reader":
        return _reader_prompt(source_prompt)
    return (
        source_prompt.rstrip()
        + f"\n\n# AutoDev {contract.role} schema-exhaustion fallback\n\n"
        f"Native {contract.role} Structured Output has exhausted its bounded schema retries. "
        f"This is the single compatibility fallback-text attempt for the same {contract.role} "
        "role, model/provider route, phase, privacy authorization, UX authority, and prepared "
        "repository evidence. Do not write or edit the durable AutoDev role artifact. Return "
        "the complete bounded role result as your final textual response in the established "
        f"{contract.role} text protocol; AutoDev Python will capture the completed OpenCode "
        "text event, parse it through the existing fallback contract, and materialize the "
        "durable artifact. Do not invent workflow-stage authority, execution classification, "
        "queue state, privacy decisions, or UX fingerprints.\n"
    )


def failure_result(
    fallback: role_runtime.RoleInvocationResult,
    *,
    elapsed_ms: int,
    retries: int,
    detail: str,
) -> role_runtime.RoleInvocationResult:
    role_name = fallback.role.capitalize() if fallback.role else "Structured role"
    return role_runtime.RoleInvocationResult(
        runtime=fallback.runtime,
        role=fallback.role,
        phase=fallback.phase,
        returncode=fallback.returncode,
        elapsed_ms=elapsed_ms,
        stdout=fallback.stdout,
        stderr=(
            f"native {role_name} schema retries exhausted; one bounded fallback-text "
            f"attempt also failed: {detail}"
        ),
        termination="structured-output-exhausted",
        model=fallback.model,
        structured_output_mode="fallback-text",
        structured_output_state=SCHEMA_FALLBACK_STATE,
        contract_name=fallback.contract_name,
        contract_version=fallback.contract_version,
        schema_retry_count=max(0, int(retries)),
    )


def _reader_prompt(source_prompt: str) -> str:
    return (
        source_prompt.rstrip()
        + "\n\n# AutoDev Reader schema-exhaustion fallback\n\n"
        "Native Reader Structured Output has exhausted its bounded schema retries. "
        "This is the single compatibility fallback-text attempt for the same Reader "
        "role, model, privacy route, and prepared repository evidence. Do not write or "
        "edit `.autodev-run/current/reader-brief.md`. Return the complete bounded factual "
        "Reader handoff as your final textual response; AutoDev Python will capture the "
        "completed OpenCode text event and materialize the durable Reader artifact. "
        "Do not return or invent workflow stage, execution classification, queue state, "
        "manual-attention decisions, external-boundary decisions, or UX fingerprints.\n"
    )
