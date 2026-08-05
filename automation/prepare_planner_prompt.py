from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from automation import run_real_issue
from automation.model_providers import ModelConfig, ollama_command_for_model


DEFAULT_READER_MODEL = run_real_issue.DEFAULT_READER_MODEL
DEFAULT_CODER_MODEL = run_real_issue.DEFAULT_CODER_MODEL


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prepare an area-reader grounded AutoDev planner prompt.")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--current-dir", required=True)
    parser.add_argument("--issue-file", required=True)
    parser.add_argument("--local-check", default="")
    parser.add_argument("--stack-context", default="")
    parser.add_argument("--labels-json", default="[]")
    parser.add_argument("--reader-provider", default="command")
    parser.add_argument("--reader-model", default=DEFAULT_READER_MODEL)
    parser.add_argument("--reader-command", default="")
    parser.add_argument("--coder-provider", default="command")
    parser.add_argument("--coder-model", default=DEFAULT_CODER_MODEL)
    parser.add_argument("--coder-command", default="")
    return parser


def text_provider_config(role: str, provider: str, model: str, command: str) -> ModelConfig:
    normalized_provider = provider or "command"
    normalized_model = model or (DEFAULT_READER_MODEL if role == "reader" else DEFAULT_CODER_MODEL)
    normalized_command = command
    if normalized_provider == "ollama":
        normalized_provider = "command"
        normalized_command = normalized_command or ollama_command_for_model(normalized_model)
    elif normalized_provider == "command" and not normalized_command:
        normalized_command = ollama_command_for_model(normalized_model)
    return ModelConfig(provider=normalized_provider, model=normalized_model, command=normalized_command)


def parse_labels(value: str) -> list[str]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(label) for label in parsed if str(label).strip()]


def run(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo = Path(args.repo).resolve()
    current_dir = Path(args.current_dir)
    issue_text = Path(args.issue_file).read_text(encoding="utf-8")
    reader_config = text_provider_config("reader", args.reader_provider, args.reader_model, args.reader_command)
    coder_config = text_provider_config("coder", args.coder_provider, args.coder_model, args.coder_command)

    try:
        area_out = current_dir / "area-reader-debug"
        run_real_issue.run_area_reader(repo, issue_text, reader_config, coder_config, area_out, sys.stdout)
        run_real_issue.write_operational_outputs(issue_text, area_out, current_dir, keep_debug=False)
        prompt = run_real_issue.build_planner_prompt_from_area_reader(
            current_dir,
            issue_text,
            args.local_check,
            parse_labels(args.labels_json),
            args.stack_context,
        )
        run_real_issue.write_text(current_dir / "planner.md", prompt)
        return 0
    except Exception as exc:
        print(f"area-reader planner preparation failed: {exc}", file=sys.stderr)
        return 1


def main() -> int:
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
