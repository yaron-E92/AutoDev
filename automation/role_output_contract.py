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

    if contract.role == "verifier":
        target = current / contract.output_artifact
        target.write_text(
            json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return target
    raise RoleOutputContractError(
        f"structured output materialization is not implemented for role {contract.role}"
    )


def validate_ux_references(
    current: Path,
    role: str,
    payload: dict[str, object],
) -> None:
    findings = payload.get("ux_findings", [])
    if findings in (None, []):
        return
    if not isinstance(findings, list) or len(findings) > MAX_UX_FINDINGS:
        raise RoleOutputContractError("ux_findings must be a bounded JSON array")

    context_path = current / f"ux-context-{role}.json"
    context = _read_json(context_path)
    if not context:
        raise RoleOutputContractError(
            "structured output contains UX findings but no effective AutoDev UX context is active"
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
    for index, finding in enumerate(findings):
        if not isinstance(finding, dict):
            raise RoleOutputContractError(f"ux finding {index} must be an object")
        kind = str(finding.get("source_kind", "") or "")
        source_id = str(finding.get("source_id", "") or "").strip()
        if kind not in allowed or not source_id:
            raise RoleOutputContractError(
                f"ux finding {index} has an invalid source_kind/source_id"
            )
        if source_id not in allowed[kind]:
            raise RoleOutputContractError(
                f"ux finding {index} references {kind} {source_id!r} outside the effective selected UX context"
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
