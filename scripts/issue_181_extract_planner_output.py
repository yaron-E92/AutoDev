from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

PLANNER_SOURCE = '''from __future__ import annotations

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
        block = normalize_plan_headings("\\n".join(lines[start:end]).strip())
        if contains_all_required_plan_sections(block) and contains_required_plan_sections_in_order(block):
            candidates.append(block)
    if not candidates:
        raise PlannerOutputError("planner output did not contain a complete six-section final plan")
    return candidates[-1].strip() + "\\n"


def normalize_plan_headings(value: str) -> str:
    headings = "|".join(re.escape(heading) for heading in REQUIRED_PLAN_HEADINGS)
    return re.sub(r"(?m)^\\s*#+\\s*(?=" + headings + r"\\b)", "", value)


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
    return re.search(r"(?m)^\\s*(?:#+\\s*)?" + re.escape(heading) + r"\\b", value)


def is_first_plan_heading(value: str) -> bool:
    return bool(re.match(r"^\\s*(?:#\\s*)?" + re.escape(REQUIRED_PLAN_HEADINGS[0]) + r"\\b", value))


def contains_hidden_reasoning(value: str) -> bool:
    lowered = value.casefold()
    if "<think>" in lowered or "</think>" in lowered:
        return True
    if any(marker in lowered for marker in REASONING_MARKERS):
        return True
    if re.search(r"(?i)\\b(wait|i will)\\b", value):
        return True
    return bool(re.search(r"(?im)^\\s*(thinking|scratchpad|reasoning)\\b", value))


def write_planner_parser_failure(path: Path, raw_output_file: Path, reason: str) -> None:
    _write_text(
        path,
        "# Planner Output Parser Failure\\n\\n"
        f"Reason: {reason}\\n\\n"
        f"Raw response: {raw_output_file}\\n",
    )
'''


def rewrite_imports() -> None:
    for relative in (
        "automation/opencode_adapter_roles.py",
        "automation/opencode_adapter_cli.py",
        "automation/opencode_adapter_handoff.py",
        "automation/opencode_adapter_contract.py",
        "automation/role_coordinator_runtime.py",
    ):
        path = ROOT / relative
        text = path.read_text(encoding="utf-8")
        text = text.replace("from automation.prompt_runner import (", "from automation.planner_output import (")
        text = text.replace("from automation.prompt_runner import PromptRunnerError", "from automation.planner_output import PlannerOutputError")
        text = text.replace("PromptRunnerError", "PlannerOutputError")
        path.write_text(text, encoding="utf-8")


def rewrite_planner_tests() -> None:
    old = ROOT / "tests/test_prompt_runner.py"
    text = old.read_text(encoding="utf-8")
    helper_start = text.find("def six_section_plan")
    class_start = text.find("class PromptRunnerTests")
    planner_start = text.find("    def test_planner_stdout_is_written_to_plan_file")
    verifier_start = text.find("    def test_verifier_stdout_requires_pass_or_fail_first_line")
    if min(helper_start, class_start, planner_start, verifier_start) < 0:
        raise SystemExit("prompt runner test markers missing")
    helper = text[helper_start:class_start].strip()
    planner_tests = text[planner_start:verifier_start].rstrip()
    new_test = (
        "import tempfile\n"
        "import unittest\n"
        "from pathlib import Path\n\n"
        "from automation import planner_output\n\n\n"
        + helper
        + "\n\n\nclass PlannerOutputTests(unittest.TestCase):\n"
        + planner_tests.replace("prompt_runner.", "planner_output.").replace("PromptRunnerError", "PlannerOutputError")
        + "\n\n\nif __name__ == \"__main__\":\n    unittest.main()\n"
    )
    (ROOT / "tests/test_planner_output.py").write_text(new_test, encoding="utf-8")
    old.unlink()


def prune_runner_only_tests() -> None:
    path = ROOT / "tests/test_provider_agnostic.py"
    text = path.read_text(encoding="utf-8")
    text = text.replace("from unittest import mock\n", "")
    text = text.replace("from automation import prompt_runner\n", "")
    text = text.replace("from automation.provider_contract import ModelProvider, ProviderResponse\n", "")
    start = text.find("def six_section_plan():")
    cls = text.find("class ProviderAgnosticTests")
    if start >= 0 and cls > start:
        text = text[:start] + text[cls:]
    for marker in (
        "    def test_prompt_runner_resolves_role_and_separates_telemetry(self):\n",
        "    def test_repair_alias_normalizes_to_fixer(self):\n",
    ):
        start = text.find(marker)
        if start >= 0:
            end = text.find("\n    def ", start + len(marker))
            if end < 0:
                end = text.find("\n\nif __name__", start)
            if end < 0:
                raise SystemExit(f"cannot bound runner-only provider test: {marker.strip()}")
            text = text[:start] + text[end:]
    start = text.find("class TelemetryProvider(")
    if start >= 0:
        end = text.find("\n\nclass ProviderAgnosticTests", start)
        if end < 0:
            raise SystemExit("cannot bound TelemetryProvider")
        text = text[:start] + text[end + 2 :]
    text = text.replace("import tempfile\n", "")
    path.write_text(text, encoding="utf-8")

    path = ROOT / "tests/test_semantic_verifier.py"
    text = path.read_text(encoding="utf-8")
    text = text.replace("from automation import prompt_runner\n", "")
    marker = "    def test_prompt_runner_semantic_mode_keeps_legacy_mode_available(self):\n"
    start = text.find(marker)
    if start >= 0:
        end = text.find("\n    def ", start + len(marker))
        if end < 0:
            end = text.find("\n\nif __name__", start)
        if end < 0:
            raise SystemExit("cannot bound semantic runner test")
        text = text[:start] + text[end:]
    if "mock." not in text:
        text = text.replace("from unittest import mock\n", "")
    path.write_text(text, encoding="utf-8")


def update_architecture_guard() -> None:
    path = ROOT / "tests/test_python_architecture.py"
    text = path.read_text(encoding="utf-8")
    if '    "automation.planner_output",\n' not in text:
        marker = '    "automation.role_coordinator_flow",\n'
        if marker not in text:
            raise SystemExit("architecture representative marker missing")
        text = text.replace(marker, marker + '    "automation.planner_output",\n', 1)
    if '    "automation/prompt_runner.py",\n' not in text:
        marker = '    "automation/semantic_cli.py",\n'
        if marker not in text:
            raise SystemExit("architecture removed path marker missing")
        text = text.replace(marker, marker + '    "automation/prompt_runner.py",\n', 1)
    path.write_text(text, encoding="utf-8")


def main() -> None:
    (ROOT / "automation/planner_output.py").write_text(PLANNER_SOURCE, encoding="utf-8")
    rewrite_imports()
    rewrite_planner_tests()
    prune_runner_only_tests()
    update_architecture_guard()
    (ROOT / "automation/prompt_runner.py").unlink()


if __name__ == "__main__":
    main()
