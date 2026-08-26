from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from automation import execution_classification as execution
from automation import opencode_adapter_handoff, opencode_adapter_roles, workflow_stages
from automation.opencode_adapter_contract import OpenCodeAdapterError


EXTERNAL_BOUNDARY_FIELD = "external_boundaries"
EXTERNAL_BOUNDARY_FILE = "execution-external-boundaries.json"
BOUNDARY_KINDS = {
    "unavailable-external-resource",
    "human-legal-provider-approval",
    "protected-secret-custody",
    "unsupported-external-capability",
}

_BLOCK = re.compile(
    re.escape(execution.CLASSIFICATION_BLOCK_START)
    + r"\s*(\{.*?\})\s*"
    + re.escape(execution.CLASSIFICATION_BLOCK_END),
    re.IGNORECASE | re.DOTALL,
)

# This is deliberately conservative. The typed external-boundary contract is the
# primary protection; these patterns only reject obvious contradictions where a
# purported human action is exactly the repository/tool work AutoDev exists to do.
_REPOSITORY_IMPLEMENTATION_ACTION = re.compile(
    r"\b(?:write|modify|edit|create|update|add|implement|refactor|generate|fix)\b"
    r".{0,120}\b(?:source\s+code|code|controllers?|api\s+endpoints?|endpoints?|"
    r"services?|ef(?:\s+core)?\s+migrations?|migrations?|permission\s+logic|"
    r"authorization\s+logic|frontend\s+(?:api\s+)?integration|typescript\s+types?|"
    r"schema\s+bindings?|tests?|repository\s+config(?:uration)?|repo\s+config(?:uration)?)\b",
    re.IGNORECASE | re.DOTALL,
)
_REPOSITORY_TOOL_ACTION = re.compile(
    r"\b(?:run|execute)\b.{0,100}\b(?:ef(?:\s+core)?|migrations?|tests?|build|lint|"
    r"typecheck|type-check|format(?:ter)?|dotnet|npm|pnpm|yarn|pytest|gradle|maven)\b",
    re.IGNORECASE | re.DOTALL,
)
_REPOSITORY_IMPLEMENTATION_GAP = re.compile(
    r"\b(?:missing|absent|unimplemented|not\s+implemented|needs?\s+(?:to\s+be\s+)?"
    r"(?:written|created|updated|implemented|added)|must\s+be\s+(?:written|created|updated|implemented|added))\b"
    r".{0,120}\b(?:source\s+code|controllers?|api\s+endpoints?|endpoints?|"
    r"ef(?:\s+core)?\s+migrations?|migrations?|permission\s+logic|authorization\s+logic|"
    r"frontend\s+(?:api\s+)?integration|typescript\s+types?|schema\s+bindings?|tests?|"
    r"repository\s+config(?:uration)?|repo\s+config(?:uration)?)\b",
    re.IGNORECASE | re.DOTALL,
)


class ExternalBoundaryEvidenceError(ValueError):
    pass


@dataclass(frozen=True)
class ExternalBoundaryEvidence:
    criterion: str
    boundary_kind: str
    human_action: str
    external_system: str
    unavailable_state: str
    why_unsupported: str


def _structured_block(text: str) -> dict[str, object] | None:
    match = _BLOCK.search(text or "")
    if not match:
        return None
    try:
        value = json.loads(match.group(1))
    except json.JSONDecodeError:
        # The canonical execution-classification parser owns JSON diagnostics.
        return None
    return value if isinstance(value, dict) else None


def _nonempty_string(raw: object, field: str) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise ExternalBoundaryEvidenceError(
            f"external-boundary field {field} must be a non-empty string"
        )
    return raw.strip()


def _string_list(raw: object, field: str) -> tuple[str, ...]:
    if not isinstance(raw, list):
        raise ExternalBoundaryEvidenceError(
            f"execution classification field {field} must be an array of strings before external-boundary validation"
        )
    values: list[str] = []
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            raise ExternalBoundaryEvidenceError(
                f"execution classification field {field} must contain only non-empty strings"
            )
        values.append(item.strip())
    return tuple(values)


def _normalized(values: tuple[str, ...]) -> set[str]:
    return {value.casefold() for value in values}


def _repository_only_claim(value: str) -> bool:
    return bool(
        _REPOSITORY_IMPLEMENTATION_ACTION.search(value)
        or _REPOSITORY_TOOL_ACTION.search(value)
        or _REPOSITORY_IMPLEMENTATION_GAP.search(value)
    )


def _parse_evidence(raw: object) -> tuple[ExternalBoundaryEvidence, ...]:
    if not isinstance(raw, list) or not raw:
        raise ExternalBoundaryEvidenceError(
            "mixed/manual-external Reader classifications require a non-empty external_boundaries array with affirmative unsupported-external-boundary evidence"
        )
    evidence: list[ExternalBoundaryEvidence] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ExternalBoundaryEvidenceError(
                f"external_boundaries[{index}] must be an object"
            )
        boundary_kind = _nonempty_string(
            item.get("boundary_kind"),
            f"external_boundaries[{index}].boundary_kind",
        ).casefold()
        if boundary_kind not in BOUNDARY_KINDS:
            raise ExternalBoundaryEvidenceError(
                f"external_boundaries[{index}].boundary_kind must be one of: "
                + ", ".join(sorted(BOUNDARY_KINDS))
            )
        evidence.append(
            ExternalBoundaryEvidence(
                criterion=_nonempty_string(
                    item.get("criterion"),
                    f"external_boundaries[{index}].criterion",
                ),
                boundary_kind=boundary_kind,
                human_action=_nonempty_string(
                    item.get("human_action"),
                    f"external_boundaries[{index}].human_action",
                ),
                external_system=_nonempty_string(
                    item.get("external_system"),
                    f"external_boundaries[{index}].external_system",
                ),
                unavailable_state=_nonempty_string(
                    item.get("unavailable_state"),
                    f"external_boundaries[{index}].unavailable_state",
                ),
                why_unsupported=_nonempty_string(
                    item.get("why_unsupported"),
                    f"external_boundaries[{index}].why_unsupported",
                ),
            )
        )
    return tuple(evidence)


def validate_reader_external_boundary(reader_text: str) -> tuple[ExternalBoundaryEvidence, ...]:
    raw = _structured_block(reader_text)
    if raw is None:
        # The existing execution-classification parser owns missing/invalid block
        # handling and therefore preserves the established correction diagnostics.
        return ()

    classification = str(raw.get("classification", "")).strip().casefold()
    if classification not in {execution.MIXED, execution.MANUAL_EXTERNAL}:
        boundaries = raw.get(EXTERNAL_BOUNDARY_FIELD)
        if isinstance(boundaries, list) and boundaries:
            raise ExternalBoundaryEvidenceError(
                "automatable Reader classification cannot contain external_boundaries"
            )
        return ()

    manual_criteria = _string_list(raw.get("manual_criteria"), "manual_criteria")
    human_actions = _string_list(raw.get("human_actions"), "human_actions")
    evidence = _parse_evidence(raw.get(EXTERNAL_BOUNDARY_FIELD))

    for value in (*manual_criteria, *human_actions):
        if _repository_only_claim(value):
            raise ExternalBoundaryEvidenceError(
                "manual/external classification is semantically contradictory: "
                f"{value!r} describes ordinary repository/source/build/test/migration work that AutoDev should implement with supported tools"
            )

    for item in evidence:
        if _repository_only_claim(item.criterion) or _repository_only_claim(
            item.human_action
        ) or _repository_only_claim(item.unavailable_state):
            raise ExternalBoundaryEvidenceError(
                "external-boundary evidence cannot relabel missing code, migrations, tests, configuration, or supported build/tool work as a human prerequisite"
            )

    manual_set = _normalized(manual_criteria)
    action_set = _normalized(human_actions)
    evidence_criteria = {item.criterion.casefold() for item in evidence}
    evidence_actions = {item.human_action.casefold() for item in evidence}

    missing_criteria = manual_set - evidence_criteria
    missing_actions = action_set - evidence_actions
    extra_criteria = evidence_criteria - manual_set
    extra_actions = evidence_actions - action_set
    if missing_criteria or missing_actions or extra_criteria or extra_actions:
        raise ExternalBoundaryEvidenceError(
            "external_boundaries must account exactly for every manual_criteria and human_actions entry; each claimed manual action needs affirmative external-boundary evidence"
        )

    return evidence


def _persist_external_boundary_evidence(
    current: Path,
    evidence: tuple[ExternalBoundaryEvidence, ...],
) -> None:
    path = current / EXTERNAL_BOUNDARY_FILE
    payload = [asdict(item) for item in evidence]
    if payload:
        path.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    else:
        path.unlink(missing_ok=True)

    try:
        state = workflow_stages.read_state(current)
    except workflow_stages.WorkflowStageError:
        return
    if payload:
        state["ExternalBoundaryEvidence"] = payload
        state["ExternalBoundaryEvidenceFile"] = (
            f".autodev-run/current/{EXTERNAL_BOUNDARY_FILE}"
        )
    else:
        state.pop("ExternalBoundaryEvidence", None)
        state.pop("ExternalBoundaryEvidenceFile", None)
    workflow_stages.write_state(current, state)


def reader_external_boundary_contract() -> str:
    kinds = "|".join(sorted(BOUNDARY_KINDS))
    return f"""

AutoDev unsupported-external-boundary evidence extension (mandatory for Reader downgrades):
A Reader result may classify work as `mixed` or `manual-external` only when at least one acceptance criterion genuinely depends on a state/action outside AutoDev's supported repository, GitHub, and deterministic tool boundary.

For `mixed` and `manual-external`, add this non-empty field to the SAME {execution.CLASSIFICATION_BLOCK_START} JSON object:
"external_boundaries": [
  {{
    "criterion": "exactly one manual_criteria entry",
    "boundary_kind": "{kinds}",
    "human_action": "exactly one human_actions entry",
    "external_system": "specific provider/authority/device/account/resource outside the target repository",
    "unavailable_state": "specific external resource, approval, identifier, custody state, or capability that is unavailable",
    "why_unsupported": "why available repository/GitHub/tool actions cannot satisfy this prerequisite"
  }}
]

Every manual_criteria entry and every human_actions entry must be covered exactly by these records. `automatable` should use `external_boundaries: []` (omission remains backward-compatible).

Repository implementation is NOT an external boundary. Missing controllers/API endpoints, EF Core migration creation or repository-local migration commands, permission/authorization logic, frontend API integration, TypeScript type/schema updates, tests, committed configuration, and supported build/lint/test commands are normal AutoDev work. Never put those tasks in manual_criteria/human_actions and never invent an external-boundary record merely because code is incomplete, difficult, or absent.

Valid downgrade evidence names the real unavailable external state plus why AutoDev cannot create/approve/custody it: for example provider identity validation/certificate issuance, administrator or legal approval, an externally provisioned resource identifier, protected secret custody, hardware/HSM enrollment, purchasing/account provisioning, or another concrete unsupported external capability.
"""


def _install_reader_prompt_extension() -> None:
    current = opencode_adapter_handoff._prepare_reader  # type: ignore[attr-defined]
    if getattr(current, "_autodev_external_boundary", False):
        return
    original = current

    def _prepare_reader(repo: Path, current_dir: Path, issue_text: str) -> str:
        prompt = original(repo, current_dir, issue_text)
        try:
            state = json.loads((current_dir / "state.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            state = {}
        if isinstance(state, dict) and execution.protocol_enabled(state):
            prompt += reader_external_boundary_contract()
        return prompt

    _prepare_reader._autodev_external_boundary = True  # type: ignore[attr-defined]
    opencode_adapter_handoff._prepare_reader = _prepare_reader  # type: ignore[attr-defined]


def _install_reader_acceptance_guard() -> None:
    current = opencode_adapter_roles._accept_role_once  # type: ignore[attr-defined]
    if getattr(current, "_autodev_external_boundary", False):
        return
    original = current

    def _accept_role_once(role: str, current_dir: Path, input_path: Path | None):
        evidence: tuple[ExternalBoundaryEvidence, ...] = ()
        guarded_reader = False
        if role == "reader":
            try:
                state = json.loads((current_dir / "state.json").read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                state = {}
            if isinstance(state, dict) and execution.protocol_enabled(state):
                guarded_reader = True
                source = input_path or current_dir / "reader-brief.md"
                try:
                    reader_text = source.read_text(encoding="utf-8")
                except OSError:
                    reader_text = ""
                try:
                    evidence = validate_reader_external_boundary(reader_text)
                except ExternalBoundaryEvidenceError as exc:
                    raise OpenCodeAdapterError(
                        f"reader execution-classification external-boundary contract rejected: {exc}"
                    ) from exc
        outputs = original(role, current_dir, input_path)
        if guarded_reader:
            _persist_external_boundary_evidence(current_dir, evidence)
        return outputs

    _accept_role_once._autodev_external_boundary = True  # type: ignore[attr-defined]
    opencode_adapter_roles._accept_role_once = _accept_role_once  # type: ignore[attr-defined]


def _install_reader_correction_extension() -> None:
    current = opencode_adapter_roles._reader_correction_contract  # type: ignore[attr-defined]
    if getattr(current, "_autodev_external_boundary", False):
        return
    original = current

    def _reader_correction_contract(current_dir: Path, role: str) -> str:
        text = original(current_dir, role)
        if role != "reader" or not text:
            return text
        return text + reader_external_boundary_contract().strip() + "\n\n"

    _reader_correction_contract._autodev_external_boundary = True  # type: ignore[attr-defined]
    opencode_adapter_roles._reader_correction_contract = _reader_correction_contract  # type: ignore[attr-defined]


def install() -> None:
    _install_reader_prompt_extension()
    _install_reader_acceptance_guard()
    _install_reader_correction_extension()
