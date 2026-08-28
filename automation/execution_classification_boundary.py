from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from automation import execution_classification as execution
from automation import opencode_adapter_handoff, opencode_adapter_roles, role_runtime_diagnostics, workflow_stages
from automation.opencode_adapter_contract import OpenCodeAdapterError


EXTERNAL_BOUNDARY_FIELD = "external_boundaries"
EXTERNAL_BOUNDARY_FILE = "execution-external-boundaries.json"
FALLBACK_FILE = "execution-classification-fallback.json"
OPERATOR_FALLBACK_SOURCE = "operator-fallback-after-invalid-reader-downgrade"
OPERATOR_CLASSIFICATION_FALLBACK_SOURCE = (
    "operator-fallback-after-invalid-reader-classification"
)
DETERMINISTIC_FALLBACK_SOURCE = "deterministic-fallback-after-invalid-reader-downgrade"
_BOUNDARY_REJECTION_PREFIX = "reader execution-classification external-boundary contract rejected:"
_CLASSIFICATION_REJECTION_PREFIX = "reader execution-classification contract rejected:"
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
    r".{0,120}\b(?:source\s+code|api\s+code|application\s+code|repository\s+code|"
    r"repo\s+code|typescript\s+code|\.net\s+code|controllers?|api\s+endpoints?|"
    r"endpoints?|ef(?:\s+core)?\s+migrations?|migrations?|permission\s+logic|"
    r"authorization\s+logic|frontend\s+(?:api\s+)?integration|typescript\s+types?|"
    r"schema\s+bindings?|tests?|repository\s+config(?:uration)?|repo\s+config(?:uration)?)\b",
    re.IGNORECASE | re.DOTALL,
)
_REPOSITORY_TOOL_ACTION = re.compile(
    r"(?:\b(?:run|execute)\b.{0,100}\b(?:ef(?:\s+core)?\s+migrations?|"
    r"migrations?\s+locally|repository[- ]local\s+(?:migrations?|tests?|build|lint)|"
    r"repo[- ]local\s+(?:migrations?|tests?|build|lint)|dotnet(?:\s+(?:build|test))?|"
    r"npm(?:\s+(?:test|run))?|pnpm|yarn|pytest|gradle|maven|typecheck|type-check|lint)\b)",
    re.IGNORECASE | re.DOTALL,
)
_REPOSITORY_IMPLEMENTATION_GAP = re.compile(
    r"\b(?:missing|absent|unimplemented|not\s+implemented|needs?\s+(?:to\s+be\s+)?"
    r"(?:written|created|updated|implemented|added)|must\s+be\s+(?:written|created|updated|implemented|added))\b"
    r".{0,120}\b(?:source\s+code|controllers?|api\s+endpoints?|endpoints?|"
    r"ef(?:\s+core)?\s+migrations?|migrations?|permission\s+logic|authorization\s+logic|"
    r"frontend\s+(?:api\s+)?integration|typescript\s+types?|schema\s+bindings?|"
    r"(?:unit|integration|repository|repo)\s+tests?|repository\s+config(?:uration)?|"
    r"repo\s+config(?:uration)?)\b",
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


def _rejection_reason(error: BaseException, prefix: str) -> str:
    current: BaseException | None = error
    seen: set[int] = set()
    lowered_prefix = prefix.casefold()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        text = str(current)
        lowered = text.casefold()
        index = lowered.find(lowered_prefix)
        if index >= 0:
            detail = text[index + len(prefix):].strip()
            return role_runtime_diagnostics.runtime_excerpt(detail)
        current = current.__cause__
    return ""


def _boundary_rejection_reason(error: BaseException) -> str:
    return _rejection_reason(error, _BOUNDARY_REJECTION_PREFIX)


def _classification_rejection_reason(error: BaseException) -> str:
    return _rejection_reason(error, _CLASSIFICATION_REJECTION_PREFIX)


def _reader_classification_rejection(
    error: BaseException,
) -> tuple[str, str] | None:
    boundary = _boundary_rejection_reason(error)
    if boundary:
        return "external-boundary", boundary
    classification = _classification_rejection_reason(error)
    if classification:
        return "execution-classification", classification
    return None


def _accepted_external_boundary_evidence(reader_text: str) -> bool:
    try:
        return bool(validate_reader_external_boundary(reader_text))
    except ExternalBoundaryEvidenceError:
        return False


def _repository_only_rejection(reason: str) -> bool:
    lowered = (reason or "").casefold()
    return any(
        marker in lowered
        for marker in (
            "semantically contradictory",
            "ordinary repository/source/build/test/migration work",
            "cannot relabel missing code, migrations, tests, configuration, or supported build/tool work",
        )
    )


def _reader_downgrade_is_repository_only(reader_text: str) -> bool:
    raw = _structured_block(reader_text)
    if raw is None:
        return False
    classification = str(raw.get("classification", "")).strip().casefold()
    if classification not in {execution.MIXED, execution.MANUAL_EXTERNAL}:
        return False
    try:
        manual = _string_list(raw.get("manual_criteria"), "manual_criteria")
        actions = _string_list(raw.get("human_actions"), "human_actions")
    except ExternalBoundaryEvidenceError:
        return False
    claimed = (*manual, *actions)
    return bool(claimed) and all(_repository_only_claim(value) for value in claimed)


def _rewrite_reader_classification_as_automatable(
    reader_text: str,
    report: execution.ExecutionReport,
) -> str:
    payload = {
        "classification": execution.AUTOMATABLE,
        "reason": report.reason,
        "autonomous_criteria": list(report.autonomous_criteria),
        "manual_criteria": [],
        "human_actions": [],
        "resume_evidence": [],
        "manual_prerequisite_blocks_implementation": False,
        "autonomous_subset_independent": False,
        EXTERNAL_BOUNDARY_FIELD: [],
    }
    replacement = (
        execution.CLASSIFICATION_BLOCK_START
        + "\n"
        + json.dumps(payload, indent=2, sort_keys=True)
        + "\n"
        + execution.CLASSIFICATION_BLOCK_END
    )
    if _BLOCK.search(reader_text):
        return _BLOCK.sub(lambda _match: replacement, reader_text, count=1)
    return reader_text.rstrip() + "\n\n" + replacement + "\n"


def prepare_reader_invalid_downgrade_fallback(
    current: Path,
    input_path: Path,
    first_error: BaseException,
    second_error: BaseException,
    *,
    first_reader_text: str = "",
) -> tuple[execution.ExecutionReport, str, str] | None:
    try:
        reader_text = input_path.read_text(encoding="utf-8")
    except OSError:
        return None

    first_classification_rejection = _reader_classification_rejection(first_error)
    second_classification_rejection = _reader_classification_rejection(second_error)
    issue_text = workflow_stages.read_text(current / "issue.md")
    explicit = execution.explicit_classification(issue_text)

    if (
        explicit is not None
        and explicit.classification == execution.AUTOMATABLE
    ):
        if first_classification_rejection is None:
            return None

        # Explicit operator-owned automatable intent remains authoritative
        # after the one bounded Reader correction unless either physical
        # attempt established affirmative external-boundary evidence that
        # itself passes the deterministic #213 validator.
        if _accepted_external_boundary_evidence(first_reader_text):
            return None
        if _accepted_external_boundary_evidence(reader_text):
            return None

        first_layer, first_detail = first_classification_rejection
        first_rejection = f"{first_layer}: {first_detail}"
        if second_classification_rejection is not None:
            second_layer, second_detail = second_classification_rejection
            second_rejection = f"{second_layer}: {second_detail}"
        else:
            # #223 already allowed malformed/omitted correction output after a
            # rejected downgrade. Keep that behavior for core classification
            # rejections too, provided no valid external evidence survived.
            second_layer = "reader-protocol"
            second_rejection = (
                "reader-protocol: "
                + role_runtime_diagnostics.runtime_excerpt(str(second_error))
            )

        source = (
            OPERATOR_CLASSIFICATION_FALLBACK_SOURCE
            if "execution-classification" in {first_layer, second_layer}
            else OPERATOR_FALLBACK_SOURCE
        )
        report = execution.ExecutionReport(
            classification=execution.AUTOMATABLE,
            reason=(
                "Operator automatable declaration retained after the Reader's rejected "
                "classification and bounded correction established no valid affirmative "
                "external-boundary evidence."
            ),
            source=source,
        )
    else:
        first_boundary_rejection = _boundary_rejection_reason(first_error)
        second_boundary_rejection = _boundary_rejection_reason(second_error)
        if not first_boundary_rejection or not second_boundary_rejection:
            return None
        first_rejection = first_boundary_rejection
        second_rejection = second_boundary_rejection
        if not (
            explicit is None
            and _reader_downgrade_is_repository_only(first_reader_text)
            and _reader_downgrade_is_repository_only(reader_text)
        ):
            return None
        report = execution.ExecutionReport(
            classification=execution.AUTOMATABLE,
            reason=(
                "Both rejected Reader downgrade attempts described only ordinary repository/tool work; "
                "deterministic fallback keeps the issue automatable."
            ),
            source=DETERMINISTIC_FALLBACK_SOURCE,
        )

    rewritten = _rewrite_reader_classification_as_automatable(reader_text, report)
    input_path.write_text(rewritten, encoding="utf-8")
    return report, first_rejection, second_rejection


def finalize_reader_invalid_downgrade_fallback(
    current: Path,
    report: execution.ExecutionReport,
    *,
    first_rejection: str,
    second_rejection: str,
    first_attempt: str,
    correction_attempt: str,
) -> Path:
    state = workflow_stages.read_state(current)
    execution.apply_state_fields(state, report)
    state["ExecutionClassificationFallback"] = True
    state["ExecutionClassificationFallbackReason"] = report.reason
    state["ExecutionClassificationFallbackFile"] = (
        f".autodev-run/current/{FALLBACK_FILE}"
    )
    workflow_stages.write_state(current, state)

    execution.persist_artifacts(current, report)
    (current / execution.MANUAL_ACTION_PLAN_FILE).unlink(missing_ok=True)
    _persist_external_boundary_evidence(current, ())

    diagnostics = {
        "version": 1,
        "classification": report.classification,
        "source": report.source,
        "reason": report.reason,
        "first_rejection": role_runtime_diagnostics.runtime_excerpt(first_rejection),
        "correction_rejection": role_runtime_diagnostics.runtime_excerpt(second_rejection),
        "first_attempt": first_attempt,
        "correction_attempt": correction_attempt,
        "protocol_correction_attempts": 1,
    }
    path = current / FALLBACK_FILE
    fallback_temp = path.with_suffix(path.suffix + ".tmp")
    fallback_temp.write_text(
        json.dumps(diagnostics, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    fallback_temp.replace(path)

    try:
        raw = json.loads(
            (current / workflow_stages.DIAGNOSTICS_FILE).read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        raw = {}
    run_diagnostics = raw if isinstance(raw, dict) else {}
    run_diagnostics["execution_classification_fallback"] = diagnostics
    temporary = (current / workflow_stages.DIAGNOSTICS_FILE).with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(run_diagnostics, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(current / workflow_stages.DIAGNOSTICS_FILE)
    (current / role_runtime_diagnostics.LAST_FAILURE_FILE).unlink(missing_ok=True)
    return path


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

Every manual_criteria entry and every human_actions entry must be covered exactly by these records. `automatable` should use `external_boundaries: []` (omission remains backward-compatible). All external-boundary fields must remain secret-free: describe secret type/custody state when relevant, never a password, token, private key, credential value, or other secret material.

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
    """Compatibility no-op for protocol v2.

    External-boundary validators remain available to deterministic/runtime
    evidence producers, but Reader output is no longer installed as a
    control-plane acceptance guard or correction contract.
    """
    return None
