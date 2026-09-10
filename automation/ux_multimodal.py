from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from automation import (
    role_output_contract,
    ux_capture,
    ux_multimodal_evidence,
    ux_reference_selection,
    ux_workflow,
)


RESULT_SCHEMA = "autodev.ux.multimodal-verification/v1"
RESULT_FILE = "ux-multimodal-verification.json"
CAPABILITY_UNSUPPORTED = "unsupported"
CAPABILITY_IMAGE_ATTACHMENTS = "image-attachments"
CAPABILITIES = {CAPABILITY_UNSUPPORTED, CAPABILITY_IMAGE_ATTACHMENTS}
MAX_FINDINGS = 64
MAX_REPAIR_BRIEF_CHARS = 12_000

MULTIMODAL_CONTRACT = role_output_contract.RoleOutputContract(
    role="verifier",
    name="autodev.ux-multimodal-verifier",
    version=1,
    schema_file="ux-multimodal-verifier-v1.json",
    output_artifact=RESULT_FILE,
    semantic_validator="automation.ux_multimodal.validate_model_payload",
    fallback_parser="automation.ux_multimodal.validate_model_payload",
    native_retry_count=2,
)


class UXMultimodalError(RuntimeError):
    pass


@dataclass(frozen=True)
class ReferenceImage:
    target_id: str
    source_kind: str
    source_id: str
    relative_path: str
    path: Path
    sha256: str
    mime: str
    size_bytes: int
    reference_target_id: str = ""

    @property
    def effective_reference_target_id(self) -> str:
        return self.reference_target_id or self.target_id


@dataclass(frozen=True)
class RuntimeVerificationResult:
    payload: dict[str, object]
    runtime: str
    model: str
    capability: str
    structured_output_mode: str = ""
    schema_retry_count: int = 0


class MultimodalRuntime(Protocol):
    name: str

    def multimodal_verifier_capability(self, repo: Path, *, runner, which=None) -> str: ...

    def invoke_multimodal_verifier(
        self,
        repo: Path,
        *,
        prompt: str,
        attachments: tuple[Path, ...],
        contract: role_output_contract.RoleOutputContract,
        runner,
        which=None,
    ) -> RuntimeVerificationResult: ...


def selected_reference_images(repo: Path, current: Path) -> tuple[ReferenceImage, ...]:
    repo = repo.expanduser().resolve()
    current = current.expanduser().resolve()
    context = _read_json(current / "ux-context-verifier.json")
    ux_context = context.get("ux_context", {}) if isinstance(context, dict) else {}
    if not isinstance(ux_context, dict):
        return ()
    state = _read_json(current / "state.json")
    expected = state.get("UXArtifact", {}) if isinstance(state, dict) else {}
    if not isinstance(expected, dict) or not expected:
        return ()
    artifact = ux_workflow.resolve_configured(repo, unattended=False)
    if artifact is None:
        raise UXMultimodalError("multimodal UX verification cannot resolve the pinned UX artifact")
    expected_identity = str(expected.get("immutable_identity", "") or "")
    if expected_identity and artifact.immutable_identity != expected_identity:
        raise UXMultimodalError(
            "multimodal UX verification resolved a different UX artifact than the prepared run"
        )

    try:
        specs = ux_reference_selection.selected_specs(repo, ux_context, artifact.manifest)
    except ux_reference_selection.UXReferenceSelectionError as exc:
        raise UXMultimodalError(str(exc)) from exc

    root = artifact.local_root.resolve()
    references: list[ReferenceImage] = []
    for spec in specs:
        path = (root / spec.relative_path).resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise UXMultimodalError(
                f"selected UX reference escapes artifact root: {spec.relative_path}"
            ) from exc
        if not path.is_file():
            raise UXMultimodalError(
                f"selected UX reference image is missing: {spec.relative_path}"
            )
        data = path.read_bytes()
        if not data or len(data) > ux_capture.MAX_IMAGE_BYTES:
            raise UXMultimodalError(
                "selected UX reference image has invalid size for multimodal verification: "
                f"{spec.relative_path}"
            )
        mime = ux_capture.image_mime(data)
        if not mime:
            raise UXMultimodalError(
                "selected UX reference is not a supported PNG/JPEG/GIF/WebP image: "
                f"{spec.relative_path}"
            )
        references.append(
            ReferenceImage(
                target_id=spec.target_id,
                source_kind=spec.source_kind,
                source_id=spec.source_id,
                relative_path=spec.relative_path,
                path=path,
                sha256=hashlib.sha256(data).hexdigest(),
                mime=mime,
                size_bytes=len(data),
                reference_target_id=spec.reference_target_id,
            )
        )
    return tuple(references)


def run_verification(
    repo: Path,
    runtime: object,
    *,
    runner: Callable[..., object],
    which=None,
) -> Path:
    repo = repo.expanduser().resolve()
    current = repo / ".autodev-run" / "current"
    result_path = current / RESULT_FILE
    references = selected_reference_images(repo, current)
    context = _read_json(current / "ux-context-verifier.json")
    artifact_context = context.get("ux_artifact", {}) if isinstance(context, dict) else {}
    ux_fingerprint = (
        str(context.get("ux_context_fingerprint", "") or "")
        if isinstance(context, dict)
        else ""
    )

    if not references:
        result = _base_result(
            status="not-applicable",
            artifact_context=artifact_context,
            ux_fingerprint=ux_fingerprint,
            capture_config_sha256="",
            capability=CAPABILITY_UNSUPPORTED,
            runtime=str(getattr(runtime, "name", "") or ""),
            model="",
            references=(),
            captures=(),
            findings=[],
            repair_brief="",
        )
        _write_json_atomic(result_path, result)
        _persist_manifest_identity(current, result)
        return result_path

    try:
        config = ux_capture.load_config(repo)
    except ux_capture.UXCaptureError as exc:
        return _write_unverifiable(
            current,
            result_path,
            references,
            artifact_context,
            ux_fingerprint,
            capture_config_sha256="",
            capability=CAPABILITY_UNSUPPORTED,
            runtime=str(getattr(runtime, "name", "") or ""),
            reason=f"capture configuration invalid: {exc}",
        )
    if config is None:
        return _write_unverifiable(
            current,
            result_path,
            references,
            artifact_context,
            ux_fingerprint,
            capture_config_sha256="",
            capability=CAPABILITY_UNSUPPORTED,
            runtime=str(getattr(runtime, "name", "") or ""),
            reason="selected visual UX references require .autodev/ux-capture.json",
        )

    captures: list[ux_capture.CapturedImage] = []
    try:
        for reference in references:
            captures.append(
                ux_capture.capture_target(
                    repo,
                    current,
                    config,
                    reference.target_id,
                    runner=runner,
                )
            )
    except ux_capture.UXCaptureError as exc:
        return _write_unverifiable(
            current,
            result_path,
            references,
            artifact_context,
            ux_fingerprint,
            capture_config_sha256=config.sha256,
            capability=CAPABILITY_UNSUPPORTED,
            runtime=str(getattr(runtime, "name", "") or ""),
            reason=f"implementation capture unavailable: {exc}",
            captures=tuple(captures),
        )

    capability_method = getattr(runtime, "multimodal_verifier_capability", None)
    if not callable(capability_method):
        capability = CAPABILITY_UNSUPPORTED
    else:
        try:
            capability = str(
                capability_method(repo, runner=runner, which=which)
                or CAPABILITY_UNSUPPORTED
            )
        except Exception as exc:
            return _write_unverifiable(
                current,
                result_path,
                references,
                artifact_context,
                ux_fingerprint,
                capture_config_sha256=config.sha256,
                capability=CAPABILITY_UNSUPPORTED,
                runtime=str(getattr(runtime, "name", "") or ""),
                reason=f"multimodal capability check failed: {exc}",
                captures=tuple(captures),
            )
    if capability not in CAPABILITIES:
        capability = CAPABILITY_UNSUPPORTED
    if capability != CAPABILITY_IMAGE_ATTACHMENTS:
        return _write_unverifiable(
            current,
            result_path,
            references,
            artifact_context,
            ux_fingerprint,
            capture_config_sha256=config.sha256,
            capability=capability,
            runtime=str(getattr(runtime, "name", "") or ""),
            reason="effective verifier runtime/model route has no declared image-input capability",
            captures=tuple(captures),
        )

    invoke_method = getattr(runtime, "invoke_multimodal_verifier", None)
    if not callable(invoke_method):
        return _write_unverifiable(
            current,
            result_path,
            references,
            artifact_context,
            ux_fingerprint,
            capture_config_sha256=config.sha256,
            capability=capability,
            runtime=str(getattr(runtime, "name", "") or ""),
            reason="runtime declares image capability but has no multimodal verifier invocation seam",
            captures=tuple(captures),
        )

    prompt = _verification_prompt(references, tuple(captures), context)
    attachments: list[Path] = []
    for reference, capture in zip(references, captures, strict=True):
        attachments.extend((reference.path, capture.path))
    try:
        runtime_result = invoke_method(
            repo,
            prompt=prompt,
            attachments=tuple(attachments),
            contract=MULTIMODAL_CONTRACT,
            runner=runner,
            which=which,
        )
    except Exception as exc:
        return _write_unverifiable(
            current,
            result_path,
            references,
            artifact_context,
            ux_fingerprint,
            capture_config_sha256=config.sha256,
            capability=capability,
            runtime=str(getattr(runtime, "name", "") or ""),
            reason=f"multimodal verifier invocation failed: {exc}",
            captures=tuple(captures),
        )
    if not isinstance(runtime_result, RuntimeVerificationResult):
        return _write_unverifiable(
            current,
            result_path,
            references,
            artifact_context,
            ux_fingerprint,
            capture_config_sha256=config.sha256,
            capability=capability,
            runtime=str(getattr(runtime, "name", "") or ""),
            reason="multimodal runtime returned an invalid verification result contract",
            captures=tuple(captures),
        )

    try:
        payload = validate_model_payload(
            runtime_result.payload,
            current=current,
            references=references,
            captures=tuple(captures),
        )
    except (UXMultimodalError, role_output_contract.RoleOutputContractError) as exc:
        return _write_unverifiable(
            current,
            result_path,
            references,
            artifact_context,
            ux_fingerprint,
            capture_config_sha256=config.sha256,
            capability=capability,
            runtime=runtime_result.runtime,
            model=runtime_result.model,
            reason=f"multimodal verifier output rejected: {exc}",
            captures=tuple(captures),
        )

    result = _base_result(
        status=str(payload["verdict"]),
        artifact_context=artifact_context,
        ux_fingerprint=ux_fingerprint,
        capture_config_sha256=config.sha256,
        capability=runtime_result.capability,
        runtime=runtime_result.runtime,
        model=runtime_result.model,
        references=references,
        captures=tuple(captures),
        findings=list(payload.get("ux_findings", [])),
        repair_brief=str(payload.get("repair_brief", "") or ""),
        comparisons=list(payload.get("comparisons", [])),
        structured_output_mode=runtime_result.structured_output_mode,
        schema_retry_count=runtime_result.schema_retry_count,
    )
    _write_json_atomic(result_path, result)
    _persist_manifest_identity(current, result)
    return result_path


def validate_model_payload(
    payload: object,
    *,
    current: Path,
    references: tuple[ReferenceImage, ...],
    captures: tuple[ux_capture.CapturedImage, ...],
) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise UXMultimodalError("multimodal verifier output must be a JSON object")
    verdict = str(payload.get("verdict", "") or "")
    if verdict not in {"pass", "repair", "unverifiable"}:
        raise UXMultimodalError("multimodal verifier verdict must be pass|repair|unverifiable")
    comparisons = payload.get("comparisons", [])
    findings = payload.get("ux_findings", [])
    repair_brief = str(payload.get("repair_brief", "") or "")
    if not isinstance(comparisons, list) or len(comparisons) != len(references):
        raise UXMultimodalError(
            "multimodal verifier must return exactly one comparison per target"
        )
    if not isinstance(findings, list) or len(findings) > MAX_FINDINGS:
        raise UXMultimodalError("multimodal UX findings must be a bounded array")
    if len(repair_brief) > MAX_REPAIR_BRIEF_CHARS:
        raise UXMultimodalError("multimodal repair brief exceeds the bounded limit")

    expected = {
        reference.target_id: (reference, capture)
        for reference, capture in zip(references, captures, strict=True)
    }
    seen: set[str] = set()
    statuses: list[str] = []
    for index, comparison in enumerate(comparisons):
        if not isinstance(comparison, dict):
            raise UXMultimodalError(f"multimodal comparison {index} must be an object")
        target_id = str(comparison.get("target_id", "") or "")
        if target_id in seen or target_id not in expected:
            raise UXMultimodalError(
                f"multimodal comparison {index} references an unknown/duplicate target"
            )
        seen.add(target_id)
        reference, capture = expected[target_id]
        exact = {
            "source_kind": reference.source_kind,
            "source_id": reference.source_id,
            "reference_sha256": reference.sha256,
            "implementation_sha256": capture.sha256,
        }
        for key, value in exact.items():
            if str(comparison.get(key, "") or "") != value:
                raise UXMultimodalError(
                    f"multimodal comparison {target_id} changed AutoDev-owned {key} identity"
                )
        status = str(comparison.get("status", "") or "")
        if status not in {"satisfied", "violated", "unverifiable"}:
            raise UXMultimodalError(
                f"multimodal comparison {target_id} has invalid status"
            )
        statuses.append(status)

    role_output_contract.validate_ux_references(
        current,
        "verifier",
        {"ux_findings": findings},
    )
    if any(status == "unverifiable" for status in statuses) and verdict != "unverifiable":
        raise UXMultimodalError(
            "unverifiable comparison requires an unverifiable overall verdict"
        )
    if any(status == "violated" for status in statuses) and verdict == "pass":
        raise UXMultimodalError("violated comparison cannot produce a pass verdict")
    if verdict == "pass" and any(status != "satisfied" for status in statuses):
        raise UXMultimodalError("pass requires every visual comparison to be satisfied")
    if verdict == "repair" and not any(status == "violated" for status in statuses):
        raise UXMultimodalError(
            "repair verdict requires at least one violated comparison"
        )
    if verdict == "repair" and not repair_brief.strip():
        raise UXMultimodalError("repair verdict requires a bounded repair brief")
    return dict(payload)


def load_result(current: Path) -> dict[str, object]:
    return _read_json(current.expanduser().resolve() / RESULT_FILE)


def repair_brief(current: Path) -> str:
    result = load_result(current)
    if str(result.get("status", "") or "") != "repair":
        return ""
    text = str(result.get("repair_brief", "") or "").strip()
    findings = result.get("findings", [])
    lines = ["# Validated multimodal UX repair", "", text]
    if isinstance(findings, list):
        for finding in findings:
            if not isinstance(finding, dict) or str(finding.get("status", "")) != "violated":
                continue
            lines.extend(
                [
                    "",
                    f"- {finding.get('source_kind')} `{finding.get('source_id')}`: {finding.get('required_change', '')}",
                ]
            )
    return "\n".join(lines).strip()[:MAX_REPAIR_BRIEF_CHARS]


def _write_unverifiable(
    current: Path,
    result_path: Path,
    references: tuple[ReferenceImage, ...],
    artifact_context: object,
    ux_fingerprint: str,
    *,
    capture_config_sha256: str,
    capability: str,
    runtime: str,
    reason: str,
    captures: tuple[ux_capture.CapturedImage, ...] = (),
    model: str = "",
) -> Path:
    findings = [
        {
            "source_kind": reference.source_kind,
            "source_id": reference.source_id,
            "status": "unverifiable",
            "evidence": str(reason)[:4000],
            "required_change": (
                "Provide deterministic implementation capture and an authorized "
                "image-capable verifier route."
            ),
            "category": "visual",
        }
        for reference in references
    ]
    result = _base_result(
        status="unverifiable",
        artifact_context=artifact_context,
        ux_fingerprint=ux_fingerprint,
        capture_config_sha256=capture_config_sha256,
        capability=capability,
        runtime=runtime,
        model=model,
        references=references,
        captures=captures,
        findings=findings,
        repair_brief="",
        diagnostic=str(reason)[:4000],
    )
    _write_json_atomic(result_path, result)
    _persist_manifest_identity(current, result)
    return result_path


def _base_result(
    *,
    status: str,
    artifact_context: object,
    ux_fingerprint: str,
    capture_config_sha256: str,
    capability: str,
    runtime: str,
    model: str,
    references: tuple[ReferenceImage, ...],
    captures: tuple[ux_capture.CapturedImage, ...],
    findings: list[object],
    repair_brief: str,
    comparisons: list[object] | None = None,
    structured_output_mode: str = "",
    schema_retry_count: int = 0,
    diagnostic: str = "",
) -> dict[str, object]:
    return ux_multimodal_evidence.build_result(
        result_schema=RESULT_SCHEMA,
        verification_contract=MULTIMODAL_CONTRACT.safe_metadata(),
        max_repair_brief_chars=MAX_REPAIR_BRIEF_CHARS,
        status=status,
        artifact_context=artifact_context,
        ux_fingerprint=ux_fingerprint,
        capture_config_sha256=capture_config_sha256,
        capability=capability,
        runtime=runtime,
        model=model,
        references=references,
        captures=captures,
        findings=findings,
        repair_brief=repair_brief,
        comparisons=comparisons,
        structured_output_mode=structured_output_mode,
        schema_retry_count=schema_retry_count,
        diagnostic=diagnostic,
    )


def _verification_prompt(
    references: tuple[ReferenceImage, ...],
    captures: tuple[ux_capture.CapturedImage, ...],
    context: dict[str, object],
) -> str:
    targets = []
    for index, (reference, capture) in enumerate(
        zip(references, captures, strict=True),
        start=1,
    ):
        targets.append(
            {
                "pair_index": index,
                "target_id": reference.target_id,
                "source_kind": reference.source_kind,
                "source_id": reference.source_id,
                "reference_target_id": reference.effective_reference_target_id,
                "reference_sha256": reference.sha256,
                "implementation_sha256": capture.sha256,
                "reference_attachment": 2 * index - 1,
                "implementation_attachment": 2 * index,
                "viewport": capture.target.viewport,
                "platform": capture.target.platform,
            }
        )
    ux_context = context.get("ux_context", {}) if isinstance(context, dict) else {}
    authority = {
        "contract": ux_context.get("contract", "") if isinstance(ux_context, dict) else "",
        "principles": ux_context.get("principles", "") if isinstance(ux_context, dict) else "",
        "screens": ux_context.get("screens", []) if isinstance(ux_context, dict) else [],
        "states": ux_context.get("states", []) if isinstance(ux_context, dict) else [],
        "journeys": ux_context.get("journeys", []) if isinstance(ux_context, dict) else [],
    }
    return (
        "Independently verify the implementation screenshots against the pinned UX reference images. "
        "Attachments are ordered as reference/implementation pairs exactly as declared below. Evaluate "
        "information hierarchy, required controls/content, layout relationships, meaningful spacing/density, "
        "typography hierarchy, authoritative color/theme intent, and the named UI state. Do not demand pixel "
        "identity unless the supplied authority explicitly requires it. Do not infer behavior not visible in the "
        "evidence. Mark anything that cannot be established from the supplied pair as unverifiable. AutoDev owns "
        "all target IDs and hashes: copy them exactly, never invent UX identifiers, and do not claim a full pass "
        "when any target is violated or unverifiable. Return only the requested structured contract.\n\n"
        "Pinned authority summary:\n"
        + json.dumps(authority, sort_keys=True, ensure_ascii=False)
        + "\n\nComparison targets:\n"
        + json.dumps(targets, sort_keys=True, ensure_ascii=False)
    )


def _persist_manifest_identity(current: Path, result: dict[str, object]) -> None:
    path = current / "run-manifest.json"
    if not path.is_file():
        return
    manifest = _read_json(path)
    if not manifest:
        return
    runtime = result.get("runtime", {})
    manifest["ux_multimodal_verification"] = {
        "schema": RESULT_SCHEMA,
        "status": str(result.get("status", "") or ""),
        "ux_context_fingerprint": str(result.get("ux_context_fingerprint", "") or ""),
        "capture_config_sha256": str(result.get("capture_config_sha256", "") or ""),
        "capture_identity": str(result.get("capture_identity", "") or ""),
        "verification_contract": dict(result.get("verification_contract", {}))
        if isinstance(result.get("verification_contract"), dict)
        else {},
        "runtime": dict(runtime) if isinstance(runtime, dict) else {},
    }
    _write_json_atomic(path, manifest)


def _read_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_json_atomic(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
