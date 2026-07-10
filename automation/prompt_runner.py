from __future__ import annotations

import argparse
import os
import re
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from automation.model_output_sanitizer import sanitize_model_output


PATCH_START = "BEGIN_UNIFIED_DIFF"
PATCH_END = "END_UNIFIED_DIFF"
NO_CHANGES_REQUIRED = "NO_CHANGES_REQUIRED"
DEFAULT_COMMIT_MESSAGE = "Implement AutoDev task"
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

class PromptRunnerError(RuntimeError):
    pass


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def quote_shell(value: str) -> str:
    if os.name == "nt":
        return subprocess.list2cmdline([value])
    return shlex.quote(value)


def run_command_provider(command: str, prompt: str, prompt_file: Path | None = None) -> str:
    if not command.strip():
        raise PromptRunnerError("command provider requires --command")

    temp_prompt: tempfile.NamedTemporaryFile[str] | None = None
    try:
        if prompt_file is None:
            temp_prompt = tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False)
            temp_prompt.write(prompt)
            temp_prompt.close()
            prompt_file = Path(temp_prompt.name)

        if "{prompt_file}" in command or "{prompt}" in command:
            rendered = command.replace("{prompt_file}", quote_shell(str(prompt_file)))
            rendered = rendered.replace("{prompt}", quote_shell(prompt))
            completed = subprocess.run(rendered, shell=True, text=True, capture_output=True, check=False)
        else:
            argv = shlex.split(command, posix=os.name != "nt")
            completed = subprocess.run(argv + [prompt], text=True, capture_output=True, check=False)
    finally:
        if temp_prompt is not None:
            Path(temp_prompt.name).unlink(missing_ok=True)

    if completed.returncode != 0:
        raise PromptRunnerError(provider_failure_message("command", completed))
    return completed.stdout


def run_ollama_provider(model: str, prompt: str) -> str:
    if not model.strip():
        raise PromptRunnerError("ollama provider requires --model")
    completed = subprocess.run(
        ["ollama", "run", model],
        input=prompt,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise PromptRunnerError(provider_failure_message("ollama", completed))
    return completed.stdout


def provider_failure_message(provider: str, completed: subprocess.CompletedProcess[str]) -> str:
    stderr = (completed.stderr or "").strip()
    return f"{provider} provider exited with {completed.returncode}: {stderr or provider}"


def run_provider(provider: str, prompt: str, *, model: str = "", command: str = "", prompt_file: Path | None = None) -> str:
    if provider == "command":
        return run_command_provider(command, prompt, prompt_file)
    if provider == "ollama":
        return run_ollama_provider(model, prompt)
    raise PromptRunnerError(f"unsupported provider: {provider}")


def first_line(value: str) -> str:
    return value.splitlines()[0].strip() if value.splitlines() else ""


def extract_commit_message(output: str) -> str:
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith("COMMIT_MESSAGE:"):
            return stripped.split(":", 1)[1].strip()
    return ""


def parse_no_changes_required(output: str) -> bool:
    return first_line(output) == NO_CHANGES_REQUIRED


def extract_unified_diff(output: str) -> str:
    start = output.find(PATCH_START)
    end = output.find(PATCH_END)
    if start == -1 or end == -1 or end <= start:
        raise PromptRunnerError(
            f"implementer/repair output must be {NO_CHANGES_REQUIRED} or include {PATCH_START}/{PATCH_END} markers"
        )
    start += len(PATCH_START)
    patch = output[start:end].strip()
    if not patch:
        raise PromptRunnerError("unified diff marker block was empty")
    return patch + "\n"


def apply_unified_diff(patch: str) -> None:
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, suffix=".patch") as handle:
        handle.write(patch)
        patch_path = Path(handle.name)
    try:
        for args in (["git", "apply", "--check", str(patch_path)], ["git", "apply", str(patch_path)]):
            completed = subprocess.run(args, text=True, capture_output=True, check=False)
            if completed.returncode != 0:
                stderr = (completed.stderr or completed.stdout or "").strip()
                raise PromptRunnerError(f"{' '.join(args[:3])} failed: {stderr}")
    finally:
        patch_path.unlink(missing_ok=True)


def handle_planner_output(output: str, output_file: Path) -> None:
    raw_output_file = output_file.with_name(output_file.name + ".raw")
    parser_error_file = output_file.with_name(output_file.name + ".parser-error.md")
    cleaned_output = sanitize_model_output(output, ensure_trailing_newline=True)
    write_text(raw_output_file, cleaned_output)
    try:
        plan = sanitize_planner_output(cleaned_output)
    except PromptRunnerError as exc:
        output_file.unlink(missing_ok=True)
        write_planner_parser_failure(parser_error_file, raw_output_file, str(exc))
        raise PromptRunnerError(f"{exc}; raw response: {raw_output_file}; parser failure: {parser_error_file}") from exc
    write_text(output_file, plan)


def sanitize_planner_output(output: str) -> str:
    cleaned = re.sub(r"(?is)<think>.*?</think>", "", sanitize_model_output(output)).strip()
    if not cleaned:
        raise PromptRunnerError("planner output was empty")
    plan = extract_last_complete_plan_block(cleaned)
    if contains_hidden_reasoning(plan):
        raise PromptRunnerError("planner output contained unstrippable reasoning or preamble")
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
        raise PromptRunnerError("planner output did not contain a complete six-section final plan")
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


def contains_planner_preamble(value: str) -> bool:
    return contains_hidden_reasoning(value)


def write_planner_parser_failure(path: Path, raw_output_file: Path, reason: str) -> None:
    write_text(
        path,
        "# Planner Output Parser Failure\n\n"
        f"Reason: {reason}\n\n"
        f"Raw response: {raw_output_file}\n",
    )


def handle_verifier_output(output: str, output_file: Path) -> None:
    marker = first_line(output)
    if marker not in {"PASS", "FAIL"}:
        raise PromptRunnerError("verifier output must start with PASS or FAIL")
    write_text(output_file, output)


def handle_patch_output(
    output: str,
    *,
    role: str,
    commit_message_file: Path | None = None,
    apply_patch_fn=apply_unified_diff,
) -> bool:
    if parse_no_changes_required(output):
        return False

    patch = extract_unified_diff(output)
    apply_patch_fn(patch)
    if role == "implementer" and commit_message_file is not None:
        write_text(commit_message_file, extract_commit_message(output) or DEFAULT_COMMIT_MESSAGE)
    return True


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one AutoDev prompt through a command or Ollama text provider.")
    parser.add_argument("--role", choices=["planner", "implementer", "repair", "verifier"], required=True)
    parser.add_argument("--provider", choices=["command", "ollama"], required=True)
    parser.add_argument("--model", default="")
    parser.add_argument("--command", default="")
    parser.add_argument("--prompt-file", required=True)
    parser.add_argument("--output-file", default="")
    parser.add_argument("--commit-message-file", default="")
    return parser


def run(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    prompt_file = Path(args.prompt_file)
    output_file = Path(args.output_file) if args.output_file else None
    commit_message_file = Path(args.commit_message_file) if args.commit_message_file else None

    try:
        prompt = read_text(prompt_file)
        output = run_provider(args.provider, prompt, model=args.model, command=args.command, prompt_file=prompt_file)
        if args.role == "planner":
            if output_file is None:
                raise PromptRunnerError("planner role requires --output-file")
            handle_planner_output(output, output_file)
        elif args.role == "verifier":
            if output_file is None:
                raise PromptRunnerError("verifier role requires --output-file")
            handle_verifier_output(output, output_file)
        else:
            handle_patch_output(output, role=args.role, commit_message_file=commit_message_file)
        return 0
    except PromptRunnerError as exc:
        print(str(exc), file=sys.stderr)
        return 1


def main() -> int:
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
