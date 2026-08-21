from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, replace
from pathlib import Path


AUTOMATABLE = "automatable"
MIXED = "mixed"
MANUAL_EXTERNAL = "manual-external"
CLASSIFICATIONS = {AUTOMATABLE, MIXED, MANUAL_EXTERNAL}

PROTOCOL_VERSION = 1
PROTOCOL_STATE_FIELD = "ExecutionClassificationProtocolVersion"
CLASSIFICATION_FILE = "execution-classification.json"
MANUAL_ACTION_PLAN_FILE = "manual-action-plan.md"
CLASSIFICATION_BLOCK_START = "AUTODEV_EXECUTION_CLASSIFICATION_JSON"
CLASSIFICATION_BLOCK_END = "END_AUTODEV_EXECUTION_CLASSIFICATION_JSON"
# This marker means: the declared manual prerequisite is complete and an
# automatable continuation remains. A fully manual issue with no repository
# continuation should be closed by the operator instead of adding this marker.
MANUAL_EVIDENCE_MARKER = "<!-- autodev:manual-evidence=complete -->"

_SIMPLE_DECLARATION = re.compile(
    r"<!--\s*autodev:execution\s*=\s*(automatable|mixed|manual-external)\s*-->",
    re.IGNORECASE,
)
_BLOCK = re.compile(
    re.escape(CLASSIFICATION_BLOCK_START)
    + r"\s*(\{.*?\})\s*"
    + re.escape(CLASSIFICATION_BLOCK_END),
    re.IGNORECASE | re.DOTALL,
)
_SECRET_EVIDENCE_REQUEST = re.compile(
    r"\b(?:paste|copy|provide|record|store|commit|comment)\b.{0,80}"
    r"\b(?:password|secret(?:\s+value)?|private\s+key|token|credential)\b",
    re.IGNORECASE,
)


class ExecutionClassificationError(ValueError):
    pass


@dataclass(frozen=True)
class ExecutionReport:
    classification: str
    reason: str
    autonomous_criteria: tuple[str, ...] = ()
    manual_criteria: tuple[str, ...] = ()
    human_actions: tuple[str, ...] = ()
    resume_evidence: tuple[str, ...] = ()
    manual_prerequisite_blocks_implementation: bool = False
    autonomous_subset_independent: bool = False
    source: str = "reader"
    completion_evidence_present: bool = False

    @property
    def attention_required(self) -> bool:
        if self.classification == AUTOMATABLE:
            return False
        # The explicit evidence marker is an operator-owned signal that the
        # manual prerequisite is satisfied and a repository continuation exists.
        # Reader must then re-evaluate the remaining work before implementation.
        return not self.completion_evidence_present

    @property
    def decomposition_recommended(self) -> bool:
        return (
            self.classification == MIXED
            and self.autonomous_subset_independent
            and not self.completion_evidence_present
        )

    @property
    def partial_autonomous_execution(self) -> bool:
        # #162 deliberately does not silently redefine a mixed parent issue.
        # Independent repository work should be decomposed into a child/follow-up
        # while the parent remains attention-required.
        return False

    def to_json(self) -> dict[str, object]:
        value = asdict(self)
        for name in (
            "autonomous_criteria",
            "manual_criteria",
            "human_actions",
            "resume_evidence",
        ):
            value[name] = list(value[name])
        value["attention_required"] = self.attention_required
        value["decomposition_recommended"] = self.decomposition_recommended
        value["partial_autonomous_execution"] = self.partial_autonomous_execution
        return value


def manual_evidence_present(issue_text: str) -> bool:
    return MANUAL_EVIDENCE_MARKER.casefold() in (issue_text or "").casefold()


def protocol_enabled(state: dict[str, object]) -> bool:
    return int(state.get(PROTOCOL_STATE_FIELD, 0) or 0) >= PROTOCOL_VERSION


def enable_protocol(state: dict[str, object]) -> None:
    state[PROTOCOL_STATE_FIELD] = PROTOCOL_VERSION


def _string_list(raw: object, field: str) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ExecutionClassificationError(
            f"execution classification field {field} must be an array of strings"
        )
    values: list[str] = []
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            raise ExecutionClassificationError(
                f"execution classification field {field} must contain only non-empty strings"
            )
        values.append(item.strip())
    return tuple(values)


def _report_from_mapping(
    raw: object,
    *,
    source: str,
    issue_text: str,
) -> ExecutionReport:
    if not isinstance(raw, dict):
        raise ExecutionClassificationError("execution classification must be a JSON object")
    classification = str(raw.get("classification", "")).strip().casefold()
    if classification not in CLASSIFICATIONS:
        raise ExecutionClassificationError(
            "execution classification must be automatable, mixed, or manual-external"
        )
    reason = str(raw.get("reason", "")).strip()
    if not reason:
        raise ExecutionClassificationError("execution classification reason must be non-empty")

    autonomous = _string_list(raw.get("autonomous_criteria"), "autonomous_criteria")
    manual = _string_list(raw.get("manual_criteria"), "manual_criteria")
    human_actions = _string_list(raw.get("human_actions"), "human_actions")
    resume_evidence = _string_list(raw.get("resume_evidence"), "resume_evidence")

    blocks = raw.get("manual_prerequisite_blocks_implementation", False)
    independent = raw.get("autonomous_subset_independent", False)
    if not isinstance(blocks, bool) or not isinstance(independent, bool):
        raise ExecutionClassificationError(
            "manual_prerequisite_blocks_implementation and autonomous_subset_independent must be booleans"
        )

    if classification == AUTOMATABLE:
        if manual or human_actions or resume_evidence or blocks or independent:
            raise ExecutionClassificationError(
                "automatable classification cannot contain unresolved manual criteria, human actions, resume evidence, or mixed-work flags"
            )
    elif classification == MIXED:
        if not autonomous or not manual:
            raise ExecutionClassificationError(
                "mixed classification must list both autonomous_criteria and manual_criteria"
            )
        if not human_actions or not resume_evidence:
            raise ExecutionClassificationError(
                "mixed classification must provide human_actions and secret-free resume_evidence"
            )
        if blocks and independent:
            raise ExecutionClassificationError(
                "a mixed issue cannot both block implementation on the manual prerequisite and declare the autonomous subset independent"
            )
    else:
        if not manual or not human_actions or not resume_evidence:
            raise ExecutionClassificationError(
                "manual-external classification must provide manual_criteria, human_actions, and secret-free resume_evidence"
            )
        blocks = True
        independent = False

    for item in resume_evidence:
        if _SECRET_EVIDENCE_REQUEST.search(item):
            raise ExecutionClassificationError(
                "resume evidence must be secret-free metadata/state proof; never request password, token, credential, secret value, or private-key material"
            )

    return ExecutionReport(
        classification=classification,
        reason=reason,
        autonomous_criteria=autonomous,
        manual_criteria=manual,
        human_actions=human_actions,
        resume_evidence=resume_evidence,
        manual_prerequisite_blocks_implementation=blocks,
        autonomous_subset_independent=independent,
        source=source,
        completion_evidence_present=manual_evidence_present(issue_text),
    )


def _structured_block(text: str) -> dict[str, object] | None:
    match = _BLOCK.search(text or "")
    if not match:
        return None
    try:
        value = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise ExecutionClassificationError(
            f"execution classification block contains invalid JSON: {exc.msg}"
        ) from exc
    if not isinstance(value, dict):
        raise ExecutionClassificationError(
            "execution classification block must contain one JSON object"
        )
    return value


def explicit_classification(issue_text: str) -> ExecutionReport | None:
    structured = _structured_block(issue_text)
    if structured is not None:
        return _report_from_mapping(
            structured,
            source="operator-metadata",
            issue_text=issue_text,
        )

    match = _SIMPLE_DECLARATION.search(issue_text or "")
    if not match:
        return None
    classification = match.group(1).casefold()
    if classification == MIXED:
        raise ExecutionClassificationError(
            "explicit mixed work requires a structured AUTODEV_EXECUTION_CLASSIFICATION_JSON block so autonomous and manual criteria are not conflated"
        )
    if classification == AUTOMATABLE:
        return _report_from_mapping(
            {
                "classification": AUTOMATABLE,
                "reason": "Operator explicitly declared this issue autonomously executable.",
                "autonomous_criteria": [],
                "manual_criteria": [],
                "human_actions": [],
                "resume_evidence": [],
                "manual_prerequisite_blocks_implementation": False,
                "autonomous_subset_independent": False,
            },
            source="operator-metadata",
            issue_text=issue_text,
        )
    return _report_from_mapping(
        {
            "classification": MANUAL_EXTERNAL,
            "reason": "Operator explicitly declared the substantive outcome manual/external.",
            "autonomous_criteria": [],
            "manual_criteria": [
                "Complete the manual/external acceptance criteria described in the issue."
            ],
            "human_actions": [
                "Complete the external prerequisite through the authorized human/provider workflow."
            ],
            "resume_evidence": [
                "Record only non-secret completion state or identifiers. If an automatable continuation remains, add the documented manual-evidence completion marker; otherwise close the fully manual issue."
            ],
            "manual_prerequisite_blocks_implementation": True,
            "autonomous_subset_independent": False,
        },
        source="operator-metadata",
        issue_text=issue_text,
    )


def parse_reader_classification(reader_text: str, issue_text: str) -> ExecutionReport:
    raw = _structured_block(reader_text)
    if raw is None:
        raise ExecutionClassificationError(
            "reader output is missing the required AUTODEV_EXECUTION_CLASSIFICATION_JSON block"
        )
    return _report_from_mapping(raw, source="reader", issue_text=issue_text)


def resolve_reader_classification(reader_text: str, issue_text: str) -> ExecutionReport:
    reader = parse_reader_classification(reader_text, issue_text)
    explicit = explicit_classification(issue_text)
    if explicit is None:
        return reader

    # Once the operator supplies explicit completion evidence, Reader owns a
    # fresh bounded classification of what remains. This prevents an old mixed
    # or manual declaration from permanently freezing an automatable follow-up.
    if explicit.completion_evidence_present:
        return replace(reader, source="reader-after-manual-evidence")

    # Otherwise operator metadata is authoritative, except that Reader may make
    # execution more conservative when it detects an obvious semantic mismatch.
    rank = {AUTOMATABLE: 0, MIXED: 1, MANUAL_EXTERNAL: 2}
    if rank[reader.classification] > rank[explicit.classification]:
        return replace(reader, source="reader-safety-downgrade")
    return replace(explicit, source="operator-metadata-confirmed")


def reader_contract_instructions() -> str:
    return f"""

AutoDev execution-classification contract (mandatory):
Before proposing repository work, classify whether the issue can actually be completed by supported repository/GitHub/tool actions. Do not treat documentation about a human task as completion of that task. Identity verification, purchasing, certificate issuance, hardware/HSM enrollment, administrator/provider approval, and secret custody are human/external unless the supplied tool evidence proves a supported automated path.

At the END of reader-brief.md, include exactly this marker-delimited JSON object (the JSON may span lines):
{CLASSIFICATION_BLOCK_START}
{{
  "classification": "automatable|mixed|manual-external",
  "reason": "concise factual reason",
  "autonomous_criteria": ["criteria AutoDev can satisfy"],
  "manual_criteria": ["criteria requiring human/external action"],
  "human_actions": ["concrete non-secret next actions"],
  "resume_evidence": ["secret-free state/metadata that proves the prerequisite is complete"],
  "manual_prerequisite_blocks_implementation": false,
  "autonomous_subset_independent": false
}}
{CLASSIFICATION_BLOCK_END}

Rules:
- automatable: manual_criteria, human_actions, and resume_evidence must be empty; both booleans false.
- mixed: list both autonomous and manual criteria. If code/config depends on an identity, credential, purchased resource, external identifier, or other unavailable prerequisite, set manual_prerequisite_blocks_implementation=true and autonomous_subset_independent=false. If repository-only work is independently useful before manual completion, set autonomous_subset_independent=true, but do not silently implement that subset on the parent: recommend a child/follow-up issue while the parent remains attention-required.
- manual-external: list the substantive manual criteria/actions/evidence; do not invent placeholder production identities or a documentation-only patch.
- Resume evidence must never contain or request secret values, passwords, tokens, credentials, private keys, or certificate key material. State/identifier presence and provider/GitHub metadata are acceptable.
"""


def apply_state_fields(state: dict[str, object], report: ExecutionReport) -> None:
    enable_protocol(state)
    state["ExecutionClassification"] = report.classification
    state["ExecutionClassificationSource"] = report.source
    state["ExecutionReason"] = report.reason
    state["AutonomousCriteria"] = list(report.autonomous_criteria)
    state["ManualCriteria"] = list(report.manual_criteria)
    state["HumanActions"] = list(report.human_actions)
    state["ResumeEvidence"] = list(report.resume_evidence)
    state["ManualPrerequisiteBlocksImplementation"] = (
        report.manual_prerequisite_blocks_implementation
    )
    state["AutonomousSubsetIndependent"] = report.autonomous_subset_independent
    state["ManualCompletionEvidencePresent"] = report.completion_evidence_present
    state["DecompositionRecommended"] = report.decomposition_recommended
    state["PartialAutonomousExecution"] = report.partial_autonomous_execution


def persist_artifacts(
    current: Path,
    report: ExecutionReport,
) -> tuple[Path, Path | None]:
    current.mkdir(parents=True, exist_ok=True)
    classification_path = current / CLASSIFICATION_FILE
    classification_path.write_text(
        json.dumps(report.to_json(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    plan_path: Path | None = None
    if report.classification != AUTOMATABLE:
        plan_path = current / MANUAL_ACTION_PLAN_FILE
        plan_path.write_text(render_manual_action_plan(report), encoding="utf-8")
    return classification_path, plan_path


def load_report(current: Path) -> ExecutionReport | None:
    try:
        raw = json.loads((current / CLASSIFICATION_FILE).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    try:
        classification = str(raw.get("classification", ""))
        if classification not in CLASSIFICATIONS:
            return None
        return ExecutionReport(
            classification=classification,
            reason=str(raw.get("reason", "")),
            autonomous_criteria=tuple(
                str(item)
                for item in raw.get("autonomous_criteria", [])
                if str(item)
            ),
            manual_criteria=tuple(
                str(item) for item in raw.get("manual_criteria", []) if str(item)
            ),
            human_actions=tuple(
                str(item) for item in raw.get("human_actions", []) if str(item)
            ),
            resume_evidence=tuple(
                str(item) for item in raw.get("resume_evidence", []) if str(item)
            ),
            manual_prerequisite_blocks_implementation=bool(
                raw.get("manual_prerequisite_blocks_implementation", False)
            ),
            autonomous_subset_independent=bool(
                raw.get("autonomous_subset_independent", False)
            ),
            source=str(raw.get("source", "")),
            completion_evidence_present=bool(
                raw.get("completion_evidence_present", False)
            ),
        )
    except (TypeError, ValueError):
        return None


def render_manual_action_plan(report: ExecutionReport) -> str:
    def section(
        title: str,
        values: tuple[str, ...],
        empty: str,
    ) -> list[str]:
        lines = [f"## {title}", ""]
        if values:
            lines.extend(
                f"{index}. {value}" for index, value in enumerate(values, start=1)
            )
        else:
            lines.append(empty)
        lines.append("")
        return lines

    queue_state = "attention" if report.attention_required else "re-evaluate"
    lines = [
        "# AutoDev manual/external action plan",
        "",
        f"Execution classification: {report.classification}",
        f"Source: {report.source}",
        f"Reason: {report.reason}",
        f"Queue state: {queue_state}",
        f"Manual completion evidence present: {'yes' if report.completion_evidence_present else 'no'}",
        "",
    ]
    lines.extend(
        section(
            "Autonomous criteria",
            report.autonomous_criteria,
            "None on this issue.",
        )
    )
    lines.extend(
        section(
            "Manual/external criteria",
            report.manual_criteria,
            "None.",
        )
    )
    lines.extend(
        section("Human next actions", report.human_actions, "None.")
    )
    lines.extend(
        section(
            "Resume evidence (secret-free)",
            report.resume_evidence,
            "None required.",
        )
    )
    if report.classification == MIXED:
        lines.extend(
            [
                "## Mixed-work boundary",
                "",
                (
                    "The manual prerequisite blocks implementation. Stop before Implementer/Fixer until the declared resume evidence exists."
                    if report.manual_prerequisite_blocks_implementation
                    else "The repository-only subset is independently useful, but AutoDev will not silently redefine or complete the mixed parent issue. Create/link a child or follow-up issue for the autonomous criteria while this parent remains attention-required."
                ),
                "",
            ]
        )
    lines.extend(
        [
            "## Resume signal",
            "",
            f"If the manual prerequisite is complete AND automatable repository work remains, add `{MANUAL_EVIDENCE_MARKER}` to the issue and rerun queue reconciliation/AutoDev. If no autonomous continuation remains, close the fully manual issue instead.",
            "",
            "## Evidence safety",
            "",
            "Record only non-secret state, identifiers, metadata, linked-issue state, or deterministic verification results. Never copy passwords, tokens, credentials, certificate private keys, or other secret values into issues or AutoDev run artifacts.",
            "",
        ]
    )
    return "\n".join(lines)


def scoped_issue_text(
    issue_text: str,
    report: ExecutionReport | None,
) -> str:
    if report is None or report.classification == AUTOMATABLE:
        return issue_text
    autonomous = (
        "\n".join(f"- {item}" for item in report.autonomous_criteria) or "- None"
    )
    manual = "\n".join(f"- {item}" for item in report.manual_criteria) or "- None"
    return (
        issue_text.rstrip()
        + "\n\n## AutoDev execution boundary (deterministic)\n\n"
        + f"Classification: {report.classification}\n\n"
        + "Autonomous criteria:\n"
        + autonomous
        + "\n\nManual/external criteria that MUST remain unresolved:\n"
        + manual
        + "\n\nDo not invent provider identities, credentials, purchased resources, external IDs, or secret values. "
        "Mixed parent issues must be decomposed rather than silently narrowed.\n"
    )
