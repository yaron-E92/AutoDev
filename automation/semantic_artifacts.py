from __future__ import annotations

import json
from pathlib import Path


def semantic_artifact_path(out_dir: Path, attempt: int) -> Path:
    return out_dir / "verification" / f"semantic-attempt-{attempt}.json"

def write_semantic_result(
    out_dir: Path,
    attempt: int,
    result: dict[str, object],
) -> Path:
    path = semantic_artifact_path(out_dir, attempt)
    _write_result_pair(path, result, f"Semantic Verification Attempt {attempt}")
    return path

def write_final_verdict(
    out_dir: Path,
    result: dict[str, object],
) -> Path:
    path = out_dir / "verification" / "final-verdict.json"
    _write_result_pair(path, result, "Final Semantic Verdict")
    return path

def render_semantic_summary(
    result: dict[str, object],
    title: str,
) -> str:
    lines = [
        f"# {title}",
        "",
        f"Verdict: {result.get('verdict', 'unknown')}",
        "",
        "## Requirements",
        "",
    ]
    for requirement in result.get("requirements", []):
        if not isinstance(requirement, dict):
            continue
        lines.append(
            f"- **{requirement.get('status', 'unknown')}** — "
            f"{requirement.get('criterion', '')}"
        )
        for evidence in requirement.get("evidence", []):
            lines.append(f"  - Evidence: {evidence}")
    lines.extend(["", "## Findings", ""])
    findings = result.get("findings", [])
    if not findings:
        lines.append("- None.")
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        path = f" ({finding.get('path')})" if finding.get("path") else ""
        lines.append(
            f"- **{finding.get('severity', 'unknown')}** — "
            f"{finding.get('message', '')}{path}"
        )
    repair_brief = str(result.get("repair_brief", "")).strip()
    lines.extend(["", "## Repair Brief", "", repair_brief or "None.", ""])
    return "\n".join(lines)

def _write_result_pair(
    json_path: Path,
    result: dict[str, object],
    title: str,
) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    json_path.with_suffix(".md").write_text(
        render_semantic_summary(result, title),
        encoding="utf-8",
    )
