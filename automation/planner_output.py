from __future__ import annotations

import re
from pathlib import Path

from automation.model_output_sanitizer import sanitize_model_output


REQUIRED_PLAN_HEADINGS = (
    "1) Where to look",
    "2) Files / areas likely to touch",
    "3) Assumptions",
    "4) Plan",
    "5) Risks / gotchas",
    "6) Recommended implementation approach",
)
REASONING_MARKERS = (
    "thinking...",
    "done thinking",
    "let's refine",
    "lets refine",
    "the prompt says",
    "final check",
)


class PlannerOutputError(RuntimeError):
    pass


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def handle_planner_output(output: str, output_file: Path) -> None:
    raw_output_file = output_file.with_name(output_file.name + ".raw")
    parser_error_file = output_file.with_name(output_file.name + ".parser-error.md")
    cleaned_output = sanitize_model_output(output, ensure_trailing_newline=True)
    _write_text(raw_output_file, cleaned_output)
    try:
        plan = sanitize_planner_output(cleaned_output)
    except PlannerOutputError as exc:
        output_file.unlink(missing_ok=True)
        write_planner_parser_failure(parser_error_file, raw_output_file, str(exc))
        raise PlannerOutputError(
            f"{exc}; raw response: {raw_output_file}; parser failure: {parser_error_file}"
        ) from exc
    _write_text(output_file, plan)


def sanitize_planner_output(output: str) -> str:
    cleaned = re.sub(r"(?is)<think>.*?</think>", "", sanitize_model_output(output)).strip()
    if not cleaned:
        raise PlannerOutputError("planner output was empty")
    plan = extract_last_complete_plan_block(cleaned)
    if contains_hidden_reasoning(plan):
        raise PlannerOutputError("planner output contained unstrippable reasoning or preamble")
    return sanitize_model_output(plan, ensure_trailing_newline=True)


def extract_last_complete_plan_block(value: str) -> str:
    lines = value.splitlines()
    starts = [index for index, line in enumerate(lines) if is_first_plan_heading(line)]
    candidates: list[str] = []
    for position, start in enumerate(starts):
        end = starts[position + 1] if position + 1 < len(starts) else len(lines)
        block = normalize_plan_headings("\n".join(lines[start:end]).strip())
        if contains_all_required_plan_sections(block) and contains_required_plan_sections_in_order(block):
            candidates.append(block)
    if not candidates:
        raise PlannerOutputError("planner output did not contain a complete six-section final plan")
    return candidates[-1].strip() + "\n"


def normalize_plan_headings(value: str) -> str:
    headings = "|".join(re.escape(heading) for heading in REQUIRED_PLAN_HEADINGS)
    return re.sub(r"(?m)^\s*#+\s*(?=" + headings + r"\b)", "", value)


def contains_all_required_plan_sections(value: str) -> bool:
    return all(required_heading_match(value, heading) for heading in REQUIRED_PLAN_HEADINGS)


def contains_required_plan_sections_in_order(value: str) -> bool:
    previous = -1
    for heading in REQUIRED_PLAN_HEADINGS:
        match = required_heading_match(value, heading)
        if match is None or match.start() <= previous:
            return False
        previous = match.start()
    return True


def required_heading_match(value: str, heading: str) -> re.Match[str] | None:
    return re.search(r"(?m)^\s*(?:#+\s*)?" + re.escape(heading) + r"\b", value)


def is_first_plan_heading(value: str) -> bool:
    return bool(re.match(r"^\s*(?:#\s*)?" + re.escape(REQUIRED_PLAN_HEADINGS[0]) + r"\b", value))


def contains_hidden_reasoning(value: str) -> bool:
    lowered = value.casefold()
    if "<think>" in lowered or "</think>" in lowered:
        return True
    if any(marker in lowered for marker in REASONING_MARKERS):
        return True
    if re.search(r"(?i)\b(wait|i will)\b", value):
        return True
    return bool(re.search(r"(?im)^\s*(thinking|scratchpad|reasoning)\b", value))


def write_planner_parser_failure(path: Path, raw_output_file: Path, reason: str) -> None:
    _write_text(
        path,
        "# Planner Output Parser Failure\n\n"
        f"Reason: {reason}\n\n"
        f"Raw response: {raw_output_file}\n",
    )
