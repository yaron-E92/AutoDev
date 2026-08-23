from __future__ import annotations

import json
from pathlib import Path

from automation.semantic_contract import (
    MAX_DIFF_CHARS,
    MAX_EVIDENCE_CHARS,
    MAX_REGRESSION_EVIDENCE_CHARS,
)
from automation.semantic_evidence import (
    collect_cross_file_regression_evidence,
)
from automation.semantic_schema import (
    semantic_result_template,
)
from automation.semantic_text import (
    _bounded,
    render_template,
)

def extract_acceptance_criteria(issue_text: str) -> list[str]:
    criteria: list[str] = []
    in_section = False
    for line in issue_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            heading = stripped.lstrip("#").strip().casefold()
            if in_section and heading != "acceptance criteria":
                break
            in_section = heading == "acceptance criteria"
            continue
        if in_section and stripped.startswith(("- ", "* ")):
            criterion = stripped[2:].strip()
            if criterion:
                criteria.append(criterion)
    return criteria

def build_schema_repair_prompt(
    original_prompt: str,
    invalid_output: str,
    error: str,
    *,
    expected_criteria: list[str] | None = None,
) -> str:
    template = json.dumps(
        semantic_result_template(expected_criteria),
        indent=2,
        ensure_ascii=False,
    )
    return (
        original_prompt.rstrip()
        + "\n\nYour previous response was rejected because it did not match the required JSON contract.\n"
        + f"Validation errors: {error}\n\n"
        + "Previous response:\n"
        + _bounded(invalid_output, 20_000)
        + "\n\nCorrect the complete artifact once. Start from this exact parser-compatible template, "
        + "preserve every pre-populated criterion verbatim, and use only these values: "
        + "verdict=pass|repair|blocked, status=met|missing|uncertain, "
        + "severity=blocking|warning. A clean pass may use findings: [].\n"
        + template
        + "\nDo not add Markdown fences or commentary.\n"
    )

def build_semantic_prompt(
    *,
    issue_text: str,
    synthesized_handoff: str,
    plan: str,
    changed_files: list[str],
    diff: str,
    deterministic_evidence: str,
    cross_file_regression_evidence: str = "",
    uncertainty_notes: str = "",
    template: str = "",
) -> str:
    criteria = extract_acceptance_criteria(issue_text)
    if not cross_file_regression_evidence:
        source_repo = getattr(changed_files, "repo", None)
        if isinstance(source_repo, Path):
            cross_file_regression_evidence = collect_cross_file_regression_evidence(
                source_repo,
                list(changed_files),
                diff,
            )
    values = {
        "IssueText": _bounded(issue_text, MAX_EVIDENCE_CHARS),
        "AcceptanceCriteria": (
            "\n".join(f"- {criterion}" for criterion in criteria)
            or "- No explicit acceptance-criteria section was detected. Infer only directly stated requirements."
        ),
        "SynthesizedHandoff": _bounded(synthesized_handoff, MAX_EVIDENCE_CHARS),
        "Plan": _bounded(plan, MAX_EVIDENCE_CHARS),
        "ChangedFiles": (
            "\n".join(f"- {path}" for path in changed_files)
            or "- No changed files were detected."
        ),
        "Diff": _bounded(diff, MAX_DIFF_CHARS),
        "DeterministicEvidence": _bounded(
            deterministic_evidence,
            MAX_EVIDENCE_CHARS,
        ),
        "CrossFileRegressionEvidence": (
            _bounded(cross_file_regression_evidence, MAX_REGRESSION_EVIDENCE_CHARS)
            or "No removed/changed declaration references were detected in unchanged source files."
        ),
        "UncertaintyNotes": (
            _bounded(uncertainty_notes, 10_000)
            or "No additional uncertainty notes were recorded."
        ),
    }
    return render_template(template, values) if template else default_semantic_template(values)

def build_semantic_repair_prompt(
    *,
    issue_text: str,
    plan: str,
    semantic_result: dict[str, object],
    changed_files: list[str],
    diff: str,
    template: str = "",
) -> str:
    values = {
        "IssueText": _bounded(issue_text, MAX_EVIDENCE_CHARS),
        "Plan": _bounded(plan, MAX_EVIDENCE_CHARS),
        "VerificationFailure": json.dumps(semantic_result, indent=2, sort_keys=True),
        "RepairBrief": str(semantic_result.get("repair_brief", "")),
        "ChangedFiles": (
            "\n".join(f"- {path}" for path in changed_files)
            or "- No changed files were detected."
        ),
        "Diff": _bounded(diff, MAX_DIFF_CHARS),
    }
    return render_template(template, values) if template else default_repair_template(values)

def default_semantic_template(values: dict[str, str]) -> str:
    return f"""You are the independent semantic verifier. Review only; do not edit files.

Original issue:
{values['IssueText']}

Detectable acceptance criteria:
{values['AcceptanceCriteria']}

Synthesized repository handoff:
{values['SynthesizedHandoff']}

Implementation plan:
{values['Plan']}

Changed files:
{values['ChangedFiles']}

Current diff:
{values['Diff']}

Deterministic verification evidence:
{values['DeterministicEvidence']}

Cross-file regression evidence:
{values['CrossFileRegressionEvidence']}

Uncertainty or skipped-check notes:
{values['UncertaintyNotes']}

Explicitly check removed or changed public/cross-file symbols against unchanged references before returning pass. Return JSON only with verdict pass, repair, or blocked; requirements using met, missing, or uncertain; findings using blocking or warning; and a targeted repair_brief. Warnings alone do not block.
"""

def default_repair_template(values: dict[str, str]) -> str:
    return f"""You are the fixer. Make only the targeted correction identified by the independent verifier.

Issue:
{values['IssueText']}

Plan:
{values['Plan']}

Verifier result:
{values['VerificationFailure']}

Repair brief:
{values['RepairBrief']}

Changed files:
{values['ChangedFiles']}

Current diff:
{values['Diff']}

Return NO_CHANGES_REQUIRED only when the repository already satisfies the repair brief; otherwise return BEGIN_UNIFIED_DIFF, an applicable unified diff, and END_UNIFIED_DIFF.
"""
