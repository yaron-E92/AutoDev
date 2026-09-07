from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path


CAPABILITY_NATIVE_STRICT = "native-strict"
CAPABILITY_NATIVE_VALIDATED = "native-validated"
CAPABILITY_EMULATED = "emulated"
CAPABILITY_UNSUPPORTED = "unsupported"
CAPABILITIES = {
    CAPABILITY_NATIVE_STRICT,
    CAPABILITY_NATIVE_VALIDATED,
    CAPABILITY_EMULATED,
    CAPABILITY_UNSUPPORTED,
}

SCHEMA_ROOT = Path(__file__).with_name("schemas")
MAX_UX_FINDINGS = 128
MAX_READER_EVIDENCE = 128


class RoleOutputContractError(ValueError):
    """A deterministic role-output contract failure."""


@dataclass(frozen=True)
class RoleOutputContract:
    role: str
    name: str
    version: int
    schema_file: str
    output_artifact: str
    semantic_validator: str
    fallback_parser: str
    native_retry_count: int = 2

    @property
    def identity(self) -> str:
        return f"{self.name}/v{self.version}"

    @property
    def schema_path(self) -> Path:
        return SCHEMA_ROOT / self.schema_file

    def schema(self) -> dict[str, object]:
        try:
            value = json.loads(self.schema_path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise RoleOutputContractError(
                f"role output schema is missing for {self.identity}: {self.schema_path}"
            ) from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise RoleOutputContractError(
                f"role output schema is unreadable for {self.identity}: {exc}"
            ) from exc
        if not isinstance(value, dict) or value.get("type") != "object":
            raise RoleOutputContractError(
                f"role output schema for {self.identity} must be a JSON object schema"
            )
        return value

    def schema_sha256(self) -> str:
        payload = json.dumps(
            self.schema(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def safe_metadata(self) -> dict[str, object]:
        return {
            "name": self.name,
            "version": self.version,
            "identity": self.identity,
            "schema_sha256": self.schema_sha256(),
            "native_retry_count": self.native_retry_count,
        }


_CONTRACTS = {
    "reader": RoleOutputContract(
        role="reader",
        name="autodev.reader",
        version=1,
        schema_file="reader-v1.json",
        output_artifact="reader-brief.md",
        semantic_validator="automation.opencode_adapter_handoff._bounded_result",
        fallback_parser="automation.opencode_adapter_handoff._bounded_result",
        native_retry_count=2,
    ),
    "verifier": RoleOutputContract(
        role="verifier",
        name="autodev.semantic-verifier",
        version=1,
        schema_file="verifier-v1.json",
        output_artifact="verification-result.json",
        semantic_validator="automation.semantic_schema.parse_semantic_output",
        fallback_parser="automation.semantic_schema.parse_semantic_output",
        native_retry_count=2,
    ),
    "planner": RoleOutputContract(
        role="planner",
        name="autodev.planner",
        version=1,
        schema_file="planner-v1.json",
        output_artifact="plan.md",
        semantic_validator="automation.planner_output.sanitize_planner_output",
        fallback_parser="automation.planner_output.sanitize_planner_output",
        native_retry_count=2,
    ),
    "synthesizer": RoleOutputContract(
        role="synthesizer",
        name="autodev.synthesizer",
        version=1,
        schema_file="synthesizer-v1.json",
        output_artifact="synthesized-handoff.md",
        semantic_validator="automation.opencode_adapter_handoff._bounded_result",
        fallback_parser="automation.opencode_adapter_handoff._bounded_result",
        native_retry_count=2,
    ),
}


def contract_for_role(role: str) -> RoleOutputContract | None:
    return _CONTRACTS.get(str(role or "").strip().casefold())


def validate_capability(value: str) -> str:
    capability = str(value or "").strip().casefold()
    if capability not in CAPABILITIES:
        raise RoleOutputContractError(
            f"unknown structured-output capability {value!r}; expected one of "
            + ", ".join(sorted(CAPABILITIES))
        )
    return capability


def native_prompt_suffix(contract: RoleOutputContract) -> str:
    return (
        "\n\n# AutoDev structured output\n\n"
        f"This invocation is bound to AutoDev role-output contract `{contract.identity}`. "
        "When the runtime requests JSON-schema output, return the requested structured value "
        "through that runtime mechanism. AutoDev owns the schema identity and will materialize "
        "the protocol artifact. Do not invent or echo an AutoDev UX-context fingerprint as proof "
        "of conformance. Schema validity does not replace semantic verification.\n"
    )


def materialize_structured_output(
    repo: Path,
    contract: RoleOutputContract,
    payload: object,
) -> Path | None:
    if not isinstance(payload, dict):
        raise RoleOutputContractError(
            f"structured output for {contract.identity} must be a JSON object"
        )
    repo = repo.expanduser().resolve()
    current = repo / ".autodev-run" / "current"
    current.mkdir(parents=True, exist_ok=True)

    if contract.role == "reader":
        allowed = {"handoff_markdown", "repository_evidence", "ux"}
        unknown = sorted(set(payload) - allowed)
        if unknown:
            raise RoleOutputContractError(
                "structured Reader output contains unsupported control-plane field(s): "
                + ", ".join(unknown)
            )
        handoff = str(payload.get("handoff_markdown", "") or "").strip()
        if not handoff:
            raise RoleOutputContractError("structured Reader handoff is empty")
        target = current / contract.output_artifact
        target.write_text(handoff + "\n", encoding="utf-8")
        _write_reader_evidence_sidecar(current, payload)
        _write_ux_sidecar(current, contract.role, payload)
        return target
    if contract.role == "verifier":
        target = current / contract.output_artifact
        target.write_text(
            json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return target
    if contract.role == "planner":
        plan = payload.get("plan", {})
        if not isinstance(plan, dict):
            raise RoleOutputContractError("structured planner output is missing plan object")
        sections = (
            ("1) Where to look", "where_to_look"),
            ("2) Files / areas likely to touch", "files_areas_likely_to_touch"),
            ("3) Assumptions", "assumptions"),
            ("4) Plan", "implementation_plan"),
            ("5) Risks / gotchas", "risks_gotchas"),
            ("6) Recommended implementation approach", "recommended_implementation_approach"),
        )
        rendered: list[str] = []
        for heading, key in sections:
            value = str(plan.get(key, "") or "").strip()
            if key in {
                "where_to_look",
                "files_areas_likely_to_touch",
                "implementation_plan",
                "recommended_implementation_approach",
            } and not value:
                raise RoleOutputContractError(
                    f"structured planner output section {key} is empty"
                )
            rendered.extend((heading, value or "None identified.", ""))
        target = current / contract.output_artifact
        target.write_text("\n".join(rendered).rstrip() + "\n", encoding="utf-8")
        _write_ux_sidecar(current, contract.role, payload)
        return target
    if contract.role == "synthesizer":
        handoff = str(payload.get("handoff_markdown", "") or "").strip()
        if not handoff:
            raise RoleOutputContractError("structured synthesizer handoff is empty")
        target = current / contract.output_artifact
        target.write_text(handoff + "\n", encoding="utf-8")
        _write_ux_sidecar(current, contract.role, payload)
        return target
    raise RoleOutputContractError(
        f"structured output materialization is not implemented for role {contract.role}"
    )


def bind_role_snapshot(
    snapshot: dict[str, object],
    role: str,
    *,
    ux_context_fingerprint: str = "",
) -> dict[str, object]:
    """Bind durable role identity to AutoDev-owned contract/UX identity idempotently."""

    contract = contract_for_role(role)
    ux_fingerprint = str(ux_context_fingerprint or "")
    if contract is None and not ux_fingerprint:
        return snapshot

    safe = snapshot.get("safe_metadata", {})
    safe = dict(safe) if isinstance(safe, dict) else {}
    existing_base = str(safe.get("role_output_base_fingerprint", "") or "")
    base_fingerprint = existing_base or str(snapshot.get("fingerprint", "") or "")
    binding: dict[str, object] = {
        "contract": contract.safe_metadata() if contract is not None else {},
        "ux_context_active": bool(ux_fingerprint),
        "ux_context_fingerprint": ux_fingerprint,
    }
    if safe.get("role_output_binding") == binding and existing_base:
        return snapshot

    canonical = json.dumps(
        {
            "base_fingerprint": base_fingerprint,
            "binding": binding,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    rebound = dict(snapshot)
    rebound["fingerprint"] = hashlib.sha256(canonical).hexdigest()
    safe["role_output_base_fingerprint"] = base_fingerprint
    safe["role_output_binding"] = binding
    rebound["safe_metadata"] = safe
    return rebound


def bind_snapshot_set_to_existing_contexts(
    repo: Path,
    snapshots: dict[str, object],
) -> dict[str, object]:
    """Reproduce contract/UX bindings before resume reconciliation."""

    current = repo.expanduser().resolve() / ".autodev-run" / "current"
    for role in list(snapshots):
        snapshot = snapshots.get(role)
        if not isinstance(snapshot, dict):
            continue
        context = _read_json(current / f"ux-context-{role}.json")
        fingerprint = str(context.get("ux_context_fingerprint", "") or "")
        snapshots[role] = bind_role_snapshot(
            snapshot,
            role,
            ux_context_fingerprint=fingerprint,
        )
    return snapshots


def persist_invocation_binding(
    repo: Path,
    role: str,
    contract: RoleOutputContract | None,
    *,
    capability: str,
    mode: str,
    state: str,
    schema_retry_count: int,
    ux_context_fingerprint: str,
) -> None:
    """Persist only AutoDev-owned, content-free structured invocation identity."""

    repo = repo.expanduser().resolve()
    path = repo / ".autodev-run" / "current" / "run-manifest.json"
    if not path.is_file():
        return
    from automation import run_manifest

    try:
        manifest = run_manifest.load_manifest(path)
    except (OSError, ValueError, run_manifest.ManifestError):
        return
    records = manifest.setdefault("structured_output", {})
    if not isinstance(records, dict):
        raise RoleOutputContractError("run manifest structured_output metadata is malformed")
    records[role] = {
        "capability": validate_capability(capability),
        "mode": str(mode or ""),
        "state": str(state or ""),
        "schema_retry_count": max(0, int(schema_retry_count or 0)),
        "ux_context_active": bool(ux_context_fingerprint),
        "ux_context_fingerprint": str(ux_context_fingerprint or ""),
        "contract": contract.safe_metadata() if contract is not None else {},
    }
    run_manifest.save_manifest(path, manifest)


def invocation_binding(repo: Path, role: str) -> dict[str, object]:
    path = repo.expanduser().resolve() / ".autodev-run" / "current" / "run-manifest.json"
    if not path.is_file():
        return {}
    from automation import run_manifest

    try:
        manifest = run_manifest.load_manifest(path)
    except (OSError, ValueError, run_manifest.ManifestError):
        return {}
    records = manifest.get("structured_output", {})
    value = records.get(role, {}) if isinstance(records, dict) else {}
    return dict(value) if isinstance(value, dict) else {}


def validate_materialized_ux_sidecar(current: Path, role: str) -> None:
    """Validate native-only UX references at the same role-acceptance boundary as text output."""

    path = current / f"structured-ux-{role}.json"
    if not path.is_file():
        return
    ux = _read_json(path)
    if not ux:
        raise RoleOutputContractError(f"structured UX sidecar for {role} is malformed")
    validate_ux_references(current, role, {"ux": ux})


def validate_ux_references(
    current: Path,
    role: str,
    payload: dict[str, object],
) -> None:
    references = _ux_references(payload)
    if not references:
        return
    if len(references) > MAX_UX_FINDINGS:
        raise RoleOutputContractError("structured UX references exceed the bounded protocol limit")

    context_path = current / f"ux-context-{role}.json"
    context = _read_json(context_path)
    if not context:
        raise RoleOutputContractError(
            "structured output contains UX references but no effective AutoDev UX context is active"
        )
    ux = context.get("ux_context", {})
    if not isinstance(ux, dict):
        raise RoleOutputContractError("effective AutoDev UX context is malformed")

    allowed: dict[str, set[str]] = {
        "journey": _string_set(ux.get("journeys", [])),
        "screen": _string_set(ux.get("screens", [])),
        "state": _string_set(ux.get("states", [])),
        "contract": _path_aliases(ux.get("contract", "")),
        "principle": _path_aliases(ux.get("principles", "")),
    }
    for index, finding in enumerate(references):
        if not isinstance(finding, dict):
            raise RoleOutputContractError(f"ux reference {index} must be an object")
        kind = str(finding.get("source_kind", "") or "")
        source_id = str(finding.get("source_id", "") or "").strip()
        if kind not in allowed or not source_id:
            raise RoleOutputContractError(
                f"ux reference {index} has an invalid source_kind/source_id"
            )
        if source_id not in allowed[kind]:
            raise RoleOutputContractError(
                f"ux reference {index} references {kind} {source_id!r} outside the effective selected UX context"
            )


def _ux_references(payload: dict[str, object]) -> list[object]:
    references: list[object] = []
    findings = payload.get("ux_findings", [])
    if isinstance(findings, list):
        references.extend(findings)
    elif findings not in (None, ""):
        raise RoleOutputContractError("ux_findings must be a JSON array")
    ux = payload.get("ux", {})
    if isinstance(ux, dict):
        constraints = ux.get("constraints_addressed", [])
        if isinstance(constraints, list):
            references.extend(constraints)
        elif constraints not in (None, ""):
            raise RoleOutputContractError("ux.constraints_addressed must be a JSON array")
    elif ux not in (None, ""):
        raise RoleOutputContractError("ux must be a JSON object")
    return references


def _write_reader_evidence_sidecar(current: Path, payload: dict[str, object]) -> None:
    evidence = payload.get("repository_evidence", [])
    if not isinstance(evidence, list) or len(evidence) > MAX_READER_EVIDENCE:
        raise RoleOutputContractError("structured Reader repository_evidence must be a bounded array")
    normalized: list[dict[str, str]] = []
    for index, item in enumerate(evidence):
        if not isinstance(item, dict):
            raise RoleOutputContractError(f"Reader repository evidence {index} must be an object")
        path = str(item.get("path", "") or "").strip()
        observation = str(item.get("observation", "") or "").strip()
        if not path or not observation:
            raise RoleOutputContractError(
                f"Reader repository evidence {index} requires non-empty path and observation"
            )
        normalized.append({"path": path, "observation": observation})
    (current / "structured-reader-evidence.json").write_text(
        json.dumps(normalized, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _write_ux_sidecar(current: Path, role: str, payload: dict[str, object]) -> None:
    ux = payload.get("ux", {})
    if not isinstance(ux, dict):
        return
    path = current / f"structured-ux-{role}.json"
    path.write_text(
        json.dumps(ux, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _read_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _string_set(value: object) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {str(item).strip() for item in value if str(item).strip()}


def _path_aliases(value: object) -> set[str]:
    text = str(value or "").strip()
    if not text:
        return set()
    path = Path(text)
    aliases = {text, path.name, path.stem}
    return {item for item in aliases if item}
