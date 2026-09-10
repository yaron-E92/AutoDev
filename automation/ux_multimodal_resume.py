from __future__ import annotations

import hashlib
import json
from pathlib import Path

from automation import run_manifest, ux_capture, ux_multimodal


class UXMultimodalResumeError(RuntimeError):
    pass


def tracked(manifest: dict[str, object], current: Path) -> bool:
    return bool(manifest.get("ux_multimodal_verification")) or (
        current.expanduser().resolve() / ux_multimodal.RESULT_FILE
    ).is_file()


def summary(current: Path) -> dict[str, object]:
    """Return bounded, safe operator metadata for the current multimodal result."""

    current = current.expanduser().resolve()
    result = ux_multimodal.load_result(current)
    if not result:
        return {
            "present": False,
            "status": "",
            "evidence_identity": "",
            "input_identity": "",
            "targets": [],
            "violations": 0,
            "unverifiable": 0,
            "runtime": "",
            "model": "",
            "capability": "",
        }

    findings = result.get("findings", [])
    findings = findings if isinstance(findings, list) else []
    comparisons = result.get("comparisons", [])
    comparisons = comparisons if isinstance(comparisons, list) else []
    runtime = result.get("runtime", {})
    runtime = runtime if isinstance(runtime, dict) else {}
    targets = sorted(
        {
            str(item.get("target_id", "") or "")
            for item in comparisons
            if isinstance(item, dict) and str(item.get("target_id", "") or "")
        }
    )
    if not targets:
        references = result.get("reference_evidence", [])
        if isinstance(references, list):
            targets = sorted(
                {
                    str(item.get("target_id", "") or "")
                    for item in references
                    if isinstance(item, dict) and str(item.get("target_id", "") or "")
                }
            )

    input_identity = _input_identity(result)
    evidence_identity = run_manifest.hash_json(
        {
            "input_identity": input_identity,
            "status": str(result.get("status", "") or ""),
            "comparisons": comparisons,
            "findings": findings,
        }
    )
    return {
        "present": True,
        "status": str(result.get("status", "") or ""),
        "evidence_identity": evidence_identity,
        "input_identity": input_identity,
        "targets": targets,
        "violations": sum(
            1
            for item in findings
            if isinstance(item, dict) and str(item.get("status", "") or "") == "violated"
        ),
        "unverifiable": sum(
            1
            for item in findings
            if isinstance(item, dict) and str(item.get("status", "") or "") == "unverifiable"
        ),
        "runtime": str(runtime.get("name", "") or ""),
        "model": str(runtime.get("model", "") or ""),
        "capability": str(runtime.get("capability", "") or ""),
        "capture_identity": str(result.get("capture_identity", "") or ""),
        "ux_context_fingerprint": str(result.get("ux_context_fingerprint", "") or ""),
    }


def stale_reasons(repo: Path, current: Path) -> list[str]:
    """Recompute non-model multimodal inputs and report stale evidence fail-closed."""

    repo = repo.expanduser().resolve()
    current = current.expanduser().resolve()
    result = ux_multimodal.load_result(current)
    if not result:
        return ["multimodal UX result artifact is missing"]

    reasons: list[str] = []
    if str(result.get("schema", "") or "") != ux_multimodal.RESULT_SCHEMA:
        reasons.append("multimodal UX result schema changed")

    context = _read_json(current / "ux-context-verifier.json")
    stored_context = str(result.get("ux_context_fingerprint", "") or "")
    current_context = str(context.get("ux_context_fingerprint", "") or "")
    if stored_context != current_context:
        reasons.append("selected UX context fingerprint changed")

    state = _read_json(current / "state.json")
    expected_artifact = state.get("UXArtifact", {}) if isinstance(state, dict) else {}
    expected_artifact = expected_artifact if isinstance(expected_artifact, dict) else {}
    stored_artifact = result.get("ux_artifact", {})
    stored_artifact = stored_artifact if isinstance(stored_artifact, dict) else {}
    if str(expected_artifact.get("immutable_identity", "") or "") != str(
        stored_artifact.get("immutable_identity", "") or ""
    ):
        reasons.append("pinned UX artifact identity changed")

    if result.get("verification_contract") != ux_multimodal.MULTIMODAL_CONTRACT.safe_metadata():
        reasons.append("multimodal verifier output contract changed")

    try:
        references = ux_multimodal.selected_reference_images(repo, current)
    except Exception as exc:
        reasons.append(f"selected UX reference evidence cannot be recomputed: {_bounded(exc)}")
        references = ()
    stored_references = _indexed(result.get("reference_evidence", []))
    current_reference_ids = {item.target_id for item in references}
    if current_reference_ids != set(stored_references):
        reasons.append("selected UX reference target set changed")
    for reference in references:
        stored = stored_references.get(reference.target_id, {})
        if not stored:
            continue
        if str(stored.get("sha256", "") or "") != reference.sha256:
            reasons.append(f"selected UX reference bytes changed for {reference.target_id}")
        if str(stored.get("path", "") or "") != reference.relative_path:
            reasons.append(f"selected UX reference path changed for {reference.target_id}")

    stored_config_hash = str(result.get("capture_config_sha256", "") or "")
    stored_implementation = _indexed(result.get("implementation_evidence", []))
    capture_relevant = bool(stored_references or stored_implementation or stored_config_hash)
    config = None
    current_config_hash = ""
    if capture_relevant:
        try:
            config = ux_capture.load_config(repo)
        except ux_capture.UXCaptureError as exc:
            reasons.append(f"UX capture configuration is no longer valid: {_bounded(exc)}")
        current_config_hash = config.sha256 if config is not None else ""
        if stored_config_hash != current_config_hash:
            reasons.append("UX capture configuration changed")

    current_implementation: list[dict[str, object]] = []
    for target_id, stored in sorted(stored_implementation.items()):
        logical_id = str(stored.get("logical_id", "") or "")
        if not logical_id or Path(logical_id).name != logical_id:
            reasons.append(f"implementation evidence path is invalid for {target_id}")
            continue
        target = config.targets.get(target_id) if config is not None else None
        configured_identity = ""
        stored_viewport = str(stored.get("viewport", "") or "")
        stored_platform = str(stored.get("platform", "") or "")
        if target is None:
            reasons.append(f"capture target is no longer configured for {target_id}")
        else:
            if target.output_name != logical_id:
                reasons.append(f"capture output identity changed for {target_id}")
            if target.viewport and target.viewport != stored_viewport:
                reasons.append(f"capture viewport changed for {target_id}")
            if target.platform and target.platform != stored_platform:
                reasons.append(f"capture platform changed for {target_id}")
            configured_identity = ux_capture.configured_capture_identity(config, target_id)
            if str(stored.get("configured_identity", "") or "") != configured_identity:
                reasons.append(f"capture configured target identity changed for {target_id}")

        image = current / "ux-captures" / logical_id
        if not image.is_file():
            reasons.append(f"implementation capture is missing for {target_id}")
            continue
        data = image.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        mime = ux_capture.image_mime(data)
        if digest != str(stored.get("sha256", "") or ""):
            reasons.append(f"implementation capture bytes changed for {target_id}")
        if mime != str(stored.get("mime", "") or "") or len(data) != int(
            stored.get("size_bytes", 0) or 0
        ):
            reasons.append(f"implementation capture metadata changed for {target_id}")

        item: dict[str, object] = {
            "target_id": target_id,
            "kind": "rendered-screenshot",
            "source_kind": str(stored.get("source_kind", "") or ""),
            "source_id": str(stored.get("source_id", "") or ""),
            "logical_id": logical_id,
            "sha256": digest,
            "mime": mime,
            "size_bytes": len(data),
            "viewport": (
                target.viewport
                if target is not None and target.viewport
                else stored_viewport
            ),
            "platform": (
                target.platform
                if target is not None and target.platform
                else stored_platform
            ),
        }
        if "configured_identity" in stored or configured_identity:
            item["configured_identity"] = configured_identity or str(
                stored.get("configured_identity", "") or ""
            )
        if "runtime_identity" in stored:
            item["runtime_identity"] = str(stored.get("runtime_identity", "") or "")
        current_implementation.append(item)

    current_capture_identity = run_manifest.hash_json(
        {
            "capture_config_sha256": current_config_hash,
            "implementation": current_implementation,
        }
    )
    if stored_implementation and current_capture_identity != str(
        result.get("capture_identity", "") or ""
    ):
        reasons.append("aggregate implementation capture identity changed")

    return _dedupe(reasons)


def reconcile_completed_verification(repo: Path, current: Path, manifest_path: Path) -> list[str]:
    """Invalidate stale semantic/PR checkpoints before resume continues."""

    manifest = run_manifest.load_manifest(manifest_path)
    if not run_manifest.stage_completed(manifest, "semantic-verified"):
        return []
    if not tracked(manifest, current):
        # Legacy semantic checkpoints that predate multimodal verification are not
        # retroactively invalidated merely because this feature now exists.
        return []
    reasons = stale_reasons(repo, current)
    if not reasons:
        return []
    run_manifest.invalidate_role(
        manifest_path,
        "verifier",
        reason="multimodal UX evidence stale: " + "; ".join(reasons[:3]),
    )
    return reasons


def _input_identity(result: dict[str, object]) -> str:
    return run_manifest.hash_json(
        {
            "schema": result.get("schema", ""),
            "ux_artifact": result.get("ux_artifact", {}),
            "ux_context_fingerprint": result.get("ux_context_fingerprint", ""),
            "capture_config_sha256": result.get("capture_config_sha256", ""),
            "capture_identity": result.get("capture_identity", ""),
            "verification_contract": result.get("verification_contract", {}),
            "runtime": result.get("runtime", {}),
            "reference_evidence": result.get("reference_evidence", []),
            "implementation_evidence": result.get("implementation_evidence", []),
        }
    )


def _indexed(value: object) -> dict[str, dict[str, object]]:
    if not isinstance(value, list):
        return {}
    indexed: dict[str, dict[str, object]] = {}
    for item in value:
        if not isinstance(item, dict):
            continue
        target_id = str(item.get("target_id", "") or "")
        if target_id:
            indexed[target_id] = item
    return indexed


def _read_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _bounded(value: object) -> str:
    return " ".join(str(value or "").split())[:500]


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result
