from __future__ import annotations

import hashlib
import json
from pathlib import Path

from automation.repair_budget_contract import (
    FAILURE_REPAIR_BUDGET_EXHAUSTED,
    ROOT_FAILURE_CLASSIFICATION,
)

def failure_details(
    result: dict[str, object],
    budget: dict[str, object],
    *,
    attempt: int,
    verification_result: Path,
    repair_artifact: Path,
    verified_source_identity: str,
) -> dict[str, object]:
    requirements = []
    for item in result.get("requirements", []) if isinstance(result.get("requirements"), list) else []:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status", ""))
        if status not in {"missing", "uncertain"}:
            continue
        requirements.append(
            {
                "criterion": str(item.get("criterion", "")),
                "status": status,
                "evidence": [str(value) for value in item.get("evidence", [])]
                if isinstance(item.get("evidence"), list)
                else [],
            }
        )

    findings = []
    for item in result.get("findings", []) if isinstance(result.get("findings"), list) else []:
        if not isinstance(item, dict) or str(item.get("severity", "")) != "blocking":
            continue
        findings.append(
            {
                "severity": "blocking",
                "message": str(item.get("message", "")),
                "path": str(item.get("path", "")),
            }
        )

    fingerprint_source = {
        "result": result,
        "verified_source_identity": verified_source_identity,
    }
    fingerprint = hashlib.sha256(
        json.dumps(
            fingerprint_source,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8", errors="replace")
    ).hexdigest()
    return {
        "kind": "semantic",
        "classification": FAILURE_REPAIR_BUDGET_EXHAUSTED,
        "root_classification": ROOT_FAILURE_CLASSIFICATION,
        "attempted_repairs": attempt,
        "maximum_repairs": int(budget.get("effective_limit", 0) or 0),
        "repair_brief": str(result.get("repair_brief", "")),
        "requirements": requirements,
        "findings": findings,
        "verification_result": str(verification_result),
        "repair_artifact": str(repair_artifact),
        "verified_source_identity": verified_source_identity,
        "failure_fingerprint": fingerprint,
        "budget": budget,
    }

def concise_failure_reason(details: dict[str, object]) -> str:
    attempted = int(details.get("attempted_repairs", 0) or 0)
    maximum = int(details.get("maximum_repairs", 0) or 0)
    brief = " ".join(str(details.get("repair_brief", "")).split())
    return (
        f"semantic repair budget exhausted after {attempted}/{maximum} automatic repairs"
        + (f"; final repair: {brief}" if brief else "")
    )[:1000]

def human_failure_summary(details: dict[str, object], fallback: str = "") -> str:
    if not details:
        return fallback
    lines = [
        f"Semantic repair budget exhausted: {details.get('attempted_repairs', 0)}/{details.get('maximum_repairs', 0)} automatic repairs consumed.",
    ]
    brief = str(details.get("repair_brief", "")).strip()
    if brief:
        lines.extend(["", "Final repair brief:", brief])
    requirements = details.get("requirements", [])
    if isinstance(requirements, list) and requirements:
        lines.extend(["", "Unmet/uncertain requirements:"])
        for item in requirements:
            if isinstance(item, dict):
                lines.append(
                    f"- [{item.get('status', '')}] {item.get('criterion', '')}"
                )
    findings = details.get("findings", [])
    if isinstance(findings, list) and findings:
        lines.extend(["", "Blocking findings:"])
        for item in findings:
            if isinstance(item, dict):
                path = str(item.get("path", "")).strip()
                prefix = f"{path}: " if path else ""
                lines.append(f"- {prefix}{item.get('message', '')}")
    lines.extend(
        [
            "",
            f"Verification result: {details.get('verification_result', '')}",
            f"Verified source identity: {details.get('verified_source_identity', '')}",
            f"Failure fingerprint: {details.get('failure_fingerprint', '')}",
            "Root classification: code-repairable (automatic repair budget exhausted).",
        ]
    )
    return "\n".join(lines)
