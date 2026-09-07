from __future__ import annotations

import json
from pathlib import Path

from automation.model_output_sanitizer import sanitize_model_output
from automation.semantic_contract import (
    ALLOWED_FINDING_SEVERITIES,
    ALLOWED_REQUIREMENT_STATUSES,
    ALLOWED_VERDICTS,
    SemanticVerifierError,
)


ALLOWED_UX_SOURCE_KINDS = {"journey", "screen", "state", "contract", "principle"}
ALLOWED_UX_FINDING_STATUSES = {"satisfied", "violated", "unverifiable"}


def semantic_result_template(expected_criteria: list[str] | None = None) -> dict[str, object]:
    """Return a parser-compatible, fail-safe semantic result skeleton.

    The blocked/uncertain defaults prevent an untouched template from being mistaken
    for approval while keeping every enum value valid for parse_semantic_output().
    """
    return {
        "verdict": "blocked",
        "requirements": [
            {
                "criterion": criterion,
                "status": "uncertain",
                "evidence": [],
            }
            for criterion in (expected_criteria or [])
        ],
        "findings": [],
        "repair_brief": "",
        "ux_findings": [],
    }


def parse_semantic_output(
    output: str,
    *,
    expected_criteria: list[str] | None = None,
    current: Path | None = None,
    role: str = "verifier",
) -> dict[str, object]:
    cleaned = sanitize_model_output(output).strip()
    if not cleaned:
        raise _malformed("semantic verifier output was empty")
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise _malformed("semantic verifier output was not valid JSON") from exc
    result = parse_semantic_value(value, expected_criteria=expected_criteria)
    if current is not None:
        from automation import role_output_contract

        try:
            role_output_contract.validate_ux_references(current, role, result)
        except role_output_contract.RoleOutputContractError as exc:
            raise SemanticVerifierError(
                str(exc),
                classification="ux_reference_outside_effective_context",
            ) from exc
    return result


def parse_semantic_value(
    value: object,
    *,
    expected_criteria: list[str] | None = None,
) -> dict[str, object]:
    """Validate a decoded verifier result without granting it workflow authority."""

    schema_errors = _semantic_schema_errors(value)
    if schema_errors:
        raise _malformed(
            "semantic verifier output schema errors: " + "; ".join(schema_errors)
        )
    assert isinstance(value, dict)

    verdict = str(value["verdict"])
    requirements = _parse_requirements(value["requirements"])
    findings = _parse_findings(value["findings"])
    repair_brief = str(value.get("repair_brief", ""))
    ux_findings = _parse_ux_findings(value.get("ux_findings", []))

    if expected_criteria:
        reported = {str(item["criterion"]).strip().casefold() for item in requirements}
        missing = [
            criterion
            for criterion in expected_criteria
            if criterion.strip().casefold() not in reported
        ]
        if missing:
            raise SemanticVerifierError(
                "semantic verifier omitted acceptance criteria: " + "; ".join(missing),
                classification="incomplete_semantic_requirements",
            )

    blocking = [item for item in findings if item["severity"] == "blocking"]
    incomplete = [item for item in requirements if item["status"] != "met"]
    violated_ux = [item for item in ux_findings if item["status"] == "violated"]
    if verdict == "pass" and (blocking or incomplete or violated_ux):
        raise SemanticVerifierError(
            "semantic verifier returned pass with blocking, unmet, or violated UX requirements",
            classification="inconsistent_semantic_verdict",
        )
    if verdict == "repair" and not repair_brief.strip():
        raise SemanticVerifierError(
            "semantic verifier returned repair without a repair_brief",
            classification="inconsistent_semantic_verdict",
        )

    result: dict[str, object] = {
        "verdict": verdict,
        "requirements": requirements,
        "findings": findings,
        "repair_brief": repair_brief.strip(),
    }
    if "ux_findings" in value:
        result["ux_findings"] = ux_findings
    return result


def _semantic_schema_errors(value: object) -> list[str]:
    if not isinstance(value, dict):
        return ["top-level value must be a JSON object"]

    errors: list[str] = []
    verdict = value.get("verdict")
    if verdict not in ALLOWED_VERDICTS:
        errors.append("verdict must be pass, repair, or blocked")

    requirements = value.get("requirements")
    if not isinstance(requirements, list):
        errors.append("requirements must be an array")
    else:
        for index, item in enumerate(requirements):
            if not isinstance(item, dict):
                errors.append(f"requirement {index} must be an object")
                continue
            criterion = item.get("criterion")
            status = item.get("status")
            evidence = item.get("evidence")
            if not isinstance(criterion, str) or not criterion.strip():
                errors.append(f"requirement {index} criterion must be non-empty text")
            if status not in ALLOWED_REQUIREMENT_STATUSES:
                errors.append(
                    f"requirement {index} status must be met, missing, or uncertain"
                )
            if not isinstance(evidence, list) or any(
                not isinstance(entry, str) for entry in evidence
            ):
                errors.append(f"requirement {index} evidence must be a string array")

    findings = value.get("findings")
    if not isinstance(findings, list):
        errors.append("findings must be an array")
    else:
        for index, item in enumerate(findings):
            if not isinstance(item, dict):
                errors.append(f"finding {index} must be an object")
                continue
            severity = item.get("severity")
            message = item.get("message")
            path = item.get("path", "")
            if severity not in ALLOWED_FINDING_SEVERITIES:
                errors.append(f"finding {index} severity must be blocking or warning")
            if not isinstance(message, str) or not message.strip():
                errors.append(f"finding {index} message must be non-empty text")
            if not isinstance(path, str):
                errors.append(f"finding {index} path must be text")

    repair_brief = value.get("repair_brief", "")
    if not isinstance(repair_brief, str):
        errors.append("repair_brief must be text")

    ux_findings = value.get("ux_findings", [])
    if not isinstance(ux_findings, list):
        errors.append("ux_findings must be an array")
    else:
        for index, item in enumerate(ux_findings):
            if not isinstance(item, dict):
                errors.append(f"ux finding {index} must be an object")
                continue
            kind = item.get("source_kind")
            source_id = item.get("source_id")
            status = item.get("status")
            evidence = item.get("evidence")
            required_change = item.get("required_change")
            if kind not in ALLOWED_UX_SOURCE_KINDS:
                errors.append(
                    f"ux finding {index} source_kind must be journey, screen, state, contract, or principle"
                )
            if not isinstance(source_id, str) or not source_id.strip():
                errors.append(f"ux finding {index} source_id must be non-empty text")
            if status not in ALLOWED_UX_FINDING_STATUSES:
                errors.append(
                    f"ux finding {index} status must be satisfied, violated, or unverifiable"
                )
            if not isinstance(evidence, str):
                errors.append(f"ux finding {index} evidence must be text")
            if not isinstance(required_change, str):
                errors.append(f"ux finding {index} required_change must be text")
    return errors


def _parse_requirements(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise _malformed("semantic verifier requirements must be an array")
    requirements: list[dict[str, object]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise _malformed(f"semantic verifier requirement {index} must be an object")
        criterion = item.get("criterion")
        status = item.get("status")
        evidence = item.get("evidence")
        if not isinstance(criterion, str) or not criterion.strip():
            raise _malformed(f"semantic verifier requirement {index} has no criterion")
        if status not in ALLOWED_REQUIREMENT_STATUSES:
            raise _malformed(
                f"semantic verifier requirement {index} has an invalid status"
            )
        if not isinstance(evidence, list) or any(
            not isinstance(entry, str) for entry in evidence
        ):
            raise _malformed(
                f"semantic verifier requirement {index} evidence must be an array of strings"
            )
        requirements.append(
            {
                "criterion": criterion.strip(),
                "status": status,
                "evidence": [entry.strip() for entry in evidence if entry.strip()],
            }
        )
    return requirements


def _parse_findings(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list):
        raise _malformed("semantic verifier findings must be an array")
    findings: list[dict[str, str]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise _malformed(f"semantic verifier finding {index} must be an object")
        severity = item.get("severity")
        message = item.get("message")
        path = item.get("path", "")
        if severity not in ALLOWED_FINDING_SEVERITIES:
            raise _malformed(f"semantic verifier finding {index} has an invalid severity")
        if not isinstance(message, str) or not message.strip():
            raise _malformed(f"semantic verifier finding {index} has no message")
        if not isinstance(path, str):
            raise _malformed(f"semantic verifier finding {index} path must be text")
        findings.append(
            {
                "severity": severity,
                "message": message.strip(),
                "path": path.strip(),
            }
        )
    return findings


def _parse_ux_findings(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list):
        raise _malformed("semantic verifier ux_findings must be an array")
    results: list[dict[str, str]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise _malformed(f"semantic verifier ux finding {index} must be an object")
        kind = item.get("source_kind")
        source_id = item.get("source_id")
        status = item.get("status")
        evidence = item.get("evidence")
        required_change = item.get("required_change")
        if kind not in ALLOWED_UX_SOURCE_KINDS:
            raise _malformed(f"semantic verifier ux finding {index} has invalid source_kind")
        if not isinstance(source_id, str) or not source_id.strip():
            raise _malformed(f"semantic verifier ux finding {index} has no source_id")
        if status not in ALLOWED_UX_FINDING_STATUSES:
            raise _malformed(f"semantic verifier ux finding {index} has invalid status")
        if not isinstance(evidence, str) or not isinstance(required_change, str):
            raise _malformed(
                f"semantic verifier ux finding {index} evidence/required_change must be text"
            )
        results.append(
            {
                "source_kind": str(kind),
                "source_id": source_id.strip(),
                "status": str(status),
                "evidence": evidence.strip(),
                "required_change": required_change.strip(),
            }
        )
    return results


def _malformed(message: str) -> SemanticVerifierError:
    return SemanticVerifierError(
        message,
        classification="malformed_semantic_output",
    )
