from __future__ import annotations

import hashlib
import json
from typing import Protocol


class ReferenceEvidence(Protocol):
    target_id: str
    source_kind: str
    source_id: str
    relative_path: str
    sha256: str
    mime: str
    size_bytes: int

    @property
    def effective_reference_target_id(self) -> str: ...


class CaptureTargetEvidence(Protocol):
    target_id: str
    source_kind: str
    source_id: str
    output_name: str
    viewport: str
    platform: str


class CaptureEvidence(Protocol):
    target: CaptureTargetEvidence
    sha256: str
    mime: str
    size_bytes: int
    configured_identity: str
    runtime_identity: str


def build_result(
    *,
    result_schema: str,
    verification_contract: dict[str, object],
    max_repair_brief_chars: int,
    status: str,
    artifact_context: object,
    ux_fingerprint: str,
    capture_config_sha256: str,
    capability: str,
    runtime: str,
    model: str,
    references: tuple[ReferenceEvidence, ...],
    captures: tuple[CaptureEvidence, ...],
    findings: list[object],
    repair_brief: str,
    comparisons: list[object] | None = None,
    structured_output_mode: str = "",
    schema_retry_count: int = 0,
    diagnostic: str = "",
) -> dict[str, object]:
    reference_evidence = [
        {
            "target_id": item.target_id,
            "kind": "ux-reference-image",
            "source_kind": item.source_kind,
            "source_id": item.source_id,
            "reference_target_id": item.effective_reference_target_id,
            "path": item.relative_path,
            "sha256": item.sha256,
            "mime": item.mime,
            "size_bytes": item.size_bytes,
        }
        for item in references
    ]
    implementation_evidence = [
        {
            "target_id": item.target.target_id,
            "kind": "rendered-screenshot",
            "source_kind": item.target.source_kind,
            "source_id": item.target.source_id,
            "logical_id": item.target.output_name,
            "sha256": item.sha256,
            "mime": item.mime,
            "size_bytes": item.size_bytes,
            "viewport": item.target.viewport,
            "platform": item.target.platform,
            **(
                {"configured_identity": item.configured_identity}
                if item.configured_identity
                else {}
            ),
            **(
                {"runtime_identity": item.runtime_identity}
                if item.runtime_identity
                else {}
            ),
        }
        for item in captures
    ]
    capture_identity = hashlib.sha256(
        json.dumps(
            {
                "capture_config_sha256": capture_config_sha256,
                "implementation": implementation_evidence,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    artifact = dict(artifact_context) if isinstance(artifact_context, dict) else {}
    return {
        "schema": result_schema,
        "status": status,
        "ux_artifact": artifact,
        "ux_context_fingerprint": ux_fingerprint,
        "capture_config_sha256": capture_config_sha256,
        "capture_identity": capture_identity,
        "verification_contract": verification_contract,
        "runtime": {
            "name": runtime,
            "model": model,
            "capability": capability,
            "structured_output_mode": structured_output_mode,
            "schema_retry_count": max(0, int(schema_retry_count)),
        },
        "reference_evidence": reference_evidence,
        "implementation_evidence": implementation_evidence,
        "comparisons": list(comparisons or []),
        "findings": findings,
        "repair_brief": repair_brief[:max_repair_brief_chars],
        "diagnostic": diagnostic,
    }
