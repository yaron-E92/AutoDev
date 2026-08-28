from __future__ import annotations

import json
from pathlib import Path

from automation import (
    execution_classification as execution,
    execution_classification_boundary as execution_boundary,
    opencode_adapter_handoff,
    opencode_adapter_roles,
    workflow_stages,
)


def install() -> None:
    """Install Reader prompt/advisory hooks without making advice authoritative."""
    current_prepare = opencode_adapter_handoff._prepare_reader  # type: ignore[attr-defined]
    if not getattr(current_prepare, "_autodev_execution_classification", False):
        original_prepare = current_prepare

        def _prepare_reader(repo: Path, current: Path, issue_text: str) -> str:
            prompt = original_prepare(repo, current, issue_text)
            try:
                state = workflow_stages.read_state(current)
            except workflow_stages.WorkflowStageError:
                state = {}
            if execution.protocol_enabled(state):
                prompt += execution.reader_contract_instructions()
            return prompt

        _prepare_reader._autodev_execution_classification = True  # type: ignore[attr-defined]
        opencode_adapter_handoff._prepare_reader = _prepare_reader  # type: ignore[attr-defined]

    current_accept = opencode_adapter_roles._accept_role_once  # type: ignore[attr-defined]
    if getattr(current_accept, "_autodev_execution_classification", False):
        return
    original_accept = current_accept

    def _accept_role_once(role: str, current: Path, input_path: Path | None):
        outputs = original_accept(role, current, input_path)
        if role != "reader":
            return outputs
        try:
            state = workflow_stages.read_state(current)
        except workflow_stages.WorkflowStageError:
            return outputs

        issue_text = workflow_stages.read_text(current / "issue.md") or str(
            state.get("IssueText", "")
        )
        reader_text = workflow_stages.read_text(current / "reader-brief.md")

        # Durable v1 runs can arrive here without having crossed the v2 prepare
        # gate. Migrate the decision without changing source or requesting a
        # classification correction/model retry.
        version = int(state.get(execution.PROTOCOL_STATE_FIELD, 0) or 0)
        if (
            version < execution.PROTOCOL_VERSION
            or str(state.get("ExecutionClassification", "")) in {"", "pending-reader"}
        ):
            try:
                report = execution.classify_issue_text(issue_text)
            except execution.ExecutionClassificationError:
                report = execution.ExecutionReport(
                    classification=execution.PROBE,
                    reason=(
                        "Durable run migrated to protocol v2 with unresolved explicit "
                        "classification metadata; continue in probe until deterministic "
                        "preflight is rerun."
                    ),
                    source="durable-v1-migration",
                )
            execution.apply_state_fields(state, report)
            workflow_stages.write_state(current, state)
            execution.persist_artifacts(current, report)

        # Reader classification is advisory in protocol v2. Keep useful bounded
        # diagnostics while never rejecting the factual handoff for serialization
        # mistakes or speculative boundary claims.
        advisory: dict[str, object] = {
            "classification_block_present": (
                execution.CLASSIFICATION_BLOCK_START.casefold()
                in reader_text.casefold()
            ),
            "control_classification": str(
                workflow_stages.read_state(current).get(
                    "ExecutionClassification",
                    "",
                )
            ),
        }
        if advisory["classification_block_present"]:
            try:
                parsed = execution.parse_reader_classification(
                    reader_text,
                    issue_text,
                )
            except execution.ExecutionClassificationError as exc:
                advisory["accepted"] = False
                advisory["diagnostic"] = str(exc)[:1000]
            else:
                advisory["accepted"] = True
                advisory["reader_classification"] = parsed.classification
                if parsed.classification in {
                    execution.MIXED,
                    execution.MANUAL_EXTERNAL,
                }:
                    try:
                        evidence = execution_boundary.validate_reader_external_boundary(
                            reader_text
                        )
                    except execution_boundary.ExternalBoundaryEvidenceError as exc:
                        advisory["external_boundary_status"] = "rejected"
                        advisory["external_boundary_diagnostic"] = str(exc)[:1000]
                    else:
                        advisory["external_boundary_status"] = (
                            "validated" if evidence else "unproven"
                        )
                        advisory["external_boundary_evidence_count"] = len(evidence)

        diagnostics_path = current / workflow_stages.DIAGNOSTICS_FILE
        try:
            raw = json.loads(diagnostics_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raw = {}
        diagnostics = raw if isinstance(raw, dict) else {}
        diagnostics["reader_execution_advisory"] = advisory
        temporary = diagnostics_path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(diagnostics, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(diagnostics_path)
        return outputs

    _accept_role_once._autodev_execution_classification = True  # type: ignore[attr-defined]
    opencode_adapter_roles._accept_role_once = _accept_role_once  # type: ignore[attr-defined]
