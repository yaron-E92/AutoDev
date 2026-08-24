from __future__ import annotations

from pathlib import Path
from automation.semantic_evidence import (
    collect_changed_files,
    collect_current_diff,
)
from automation.semantic_prompts import build_semantic_repair_prompt
from automation.semantic_storage import (
    _read_json,
    _read_text,
)

def prepare_semantic_repair_prompt(
    repo: Path,
    current_dir: Path,
    template_path: Path,
    output_path: Path,
) -> None:
    state = _read_json(current_dir / "state.json")
    result = _read_json(current_dir / "verification-result.json")
    changed_files = collect_changed_files(repo)
    prompt = build_semantic_repair_prompt(
        issue_text=_read_text(current_dir / "issue.md")
        or str(state.get("IssueText", "")),
        plan=_read_text(current_dir / "plan.md"),
        semantic_result=result,
        changed_files=changed_files,
        diff=collect_current_diff(repo, changed_files),
        template=_read_text(template_path),
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(prompt, encoding="utf-8")
    repair_brief_path = current_dir / "verification" / "repair-brief.md"
    repair_brief_path.parent.mkdir(parents=True, exist_ok=True)
    repair_brief_path.write_text(
        str(result.get("repair_brief", "")).strip() + "\n",
        encoding="utf-8",
    )
