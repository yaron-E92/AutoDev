#!/usr/bin/env python3
"""Run a simple local Ollama reader-coder benchmark."""

import argparse
import json
import os
from pathlib import Path
import sys
import time
from urllib import error, request


OLLAMA_CHAT_URL = "http://localhost:11434/api/chat"
DEFAULT_MAX_CHARS = 70000
MAX_FILE_BYTES = 250000

INCLUDED_SUFFIXES = {
    ".cs",
    ".csproj",
    ".sln",
    ".xaml",
    ".xml",
    ".json",
    ".md",
    ".yml",
    ".yaml",
    ".props",
    ".targets",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".css",
    ".html",
    ".sh",
    ".ps1",
}

EXCLUDED_DIRS = {
    ".git",
    ".vs",
    ".vscode",
    "bin",
    "obj",
    "node_modules",
    ".codex-run",
    ".idea",
    ".cache",
    "TestResults",
    "dist",
    "build",
    "coverage",
}


def parse_args():
    parser = argparse.ArgumentParser(
        description="Run a baseline local Ollama reader-coder benchmark."
    )
    parser.add_argument("--repo", required=True, help="Repository to read for context.")
    parser.add_argument("--reader", required=True, help="Ollama reader model name.")
    parser.add_argument("--coder", required=True, help="Ollama coder model name.")
    parser.add_argument("--issue", required=True, help="Issue or task text.")
    parser.add_argument(
        "--max-chars",
        type=int,
        default=DEFAULT_MAX_CHARS,
        help=f"Maximum repository context bundle characters. Default: {DEFAULT_MAX_CHARS}.",
    )
    parser.add_argument("--out", required=True, help="Output directory for benchmark files.")
    return parser.parse_args()


def expand_user_path(value):
    return Path(os.path.expanduser(value)).resolve()


def write_text(path, text):
    path.write_text(text, encoding="utf-8")


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def iter_candidate_files(repo):
    for root, dirnames, filenames in os.walk(repo):
        dirnames[:] = sorted(name for name in dirnames if name not in EXCLUDED_DIRS)
        for filename in sorted(filenames):
            path = Path(root) / filename
            if path.suffix in INCLUDED_SUFFIXES:
                yield path


def collect_repo_bundle(repo, max_chars):
    if max_chars <= 0:
        raise ValueError("--max-chars must be greater than zero")

    parts = []
    remaining = max_chars
    included_files = []
    skipped_large_files = []
    skipped_unreadable_files = []
    truncated = False

    for path in iter_candidate_files(repo):
        try:
            size = path.stat().st_size
        except OSError as exc:
            skipped_unreadable_files.append({"path": str(path), "reason": str(exc)})
            continue

        if size > MAX_FILE_BYTES:
            skipped_large_files.append({"path": str(path.relative_to(repo)), "bytes": size})
            continue

        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            skipped_unreadable_files.append({"path": str(path), "reason": str(exc)})
            continue

        relative_path = path.relative_to(repo).as_posix()
        entry = f"\n\n===== FILE: {relative_path} =====\n{content.rstrip()}\n"
        if len(entry) > remaining:
            if remaining > len(f"\n\n===== FILE: {relative_path} =====\n"):
                parts.append(entry[:remaining])
                included_files.append(relative_path)
            truncated = True
            break

        parts.append(entry)
        included_files.append(relative_path)
        remaining -= len(entry)

        if remaining <= 0:
            truncated = True
            break

    bundle = "".join(parts).lstrip()
    metadata = {
        "repo": str(repo),
        "max_chars": max_chars,
        "bundle_chars": len(bundle),
        "included_file_count": len(included_files),
        "included_files": included_files,
        "skipped_large_files": skipped_large_files,
        "skipped_unreadable_files": skipped_unreadable_files,
        "truncated": truncated,
    }
    return bundle, metadata


def build_reader_prompt(issue, bundle, metadata):
    return f"""You are the reader model in a local LLM reader-coder benchmark.

You are not the coder. Do not edit files. Read the original issue and repository context, then write a compact factual handoff brief for a separate coder model.

Your brief must include:
- The likely files or areas involved, using exact file paths when possible.
- Key facts from the repository context that affect the implementation.
- Any uncertainties or missing information.
- Verification commands that are runnable from the repository root where possible.

Original issue:
{issue}

Repository context metadata:
{json.dumps(metadata, indent=2, sort_keys=True)}

Repository context bundle:
{bundle}
"""


def build_coder_prompt(issue, reader_brief):
    return f"""You are the coder model in a local LLM reader-coder benchmark.

Consume the original issue and the reader brief. Produce a minimal issue-scoped implementation plan.

Rules:
- Do not invent files.
- Do not invent placeholder commands.
- Commands must be runnable from the repository root unless an explicit cd command is included.
- For verification-only issues, do not claim files need changes.

Original issue:
{issue}

Reader brief:
{reader_brief}
"""


def call_ollama(model, prompt):
    payload = {
        "model": model,
        "stream": False,
        "think": False,
        "messages": [
            {
                "role": "user",
                "content": prompt,
            }
        ],
    }
    data = json.dumps(payload).encode("utf-8")
    http_request = request.Request(
        OLLAMA_CHAT_URL,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    started = time.monotonic()
    try:
        with request.urlopen(http_request) as response:
            response_body = response.read().decode("utf-8")
    except error.URLError as exc:
        raise RuntimeError(f"Ollama request failed for model {model}: {exc}") from exc
    wall_seconds = time.monotonic() - started

    try:
        raw = json.loads(response_body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Ollama returned invalid JSON for model {model}: {exc}") from exc

    return raw, wall_seconds


def duration_seconds(raw, key):
    value = raw.get(key)
    if not isinstance(value, (int, float)):
        return 0.0
    return value / 1_000_000_000


def tokens_per_sec(count, seconds):
    if not isinstance(count, (int, float)) or count <= 0 or seconds <= 0:
        return 0.0
    return count / seconds


def extract_message(raw):
    message = raw.get("message")
    if not isinstance(message, dict):
        return "", ""

    content = message.get("content")
    thinking = message.get("thinking")
    return (
        content if isinstance(content, str) else "",
        thinking if isinstance(thinking, str) else "",
    )


def build_metrics(raw, wall_seconds, response_text):
    prompt_eval_count = raw.get("prompt_eval_count", 0)
    eval_count = raw.get("eval_count", 0)
    prompt_eval_seconds = duration_seconds(raw, "prompt_eval_duration")
    eval_seconds = duration_seconds(raw, "eval_duration")

    return {
        "done_reason": raw.get("done_reason", ""),
        "wall_seconds": wall_seconds,
        "prompt_eval_count": prompt_eval_count,
        "prompt_eval_tokens_per_sec": tokens_per_sec(prompt_eval_count, prompt_eval_seconds),
        "eval_count": eval_count,
        "generation_tokens_per_sec": tokens_per_sec(eval_count, eval_seconds),
        "total_duration_seconds": duration_seconds(raw, "total_duration"),
        "load_duration_seconds": duration_seconds(raw, "load_duration"),
        "response_chars": len(response_text),
    }


def main():
    args = parse_args()
    repo = expand_user_path(args.repo)
    out = expand_user_path(args.out)

    if not repo.is_dir():
        print(f"--repo is not a directory: {repo}", file=sys.stderr)
        return 2

    out.mkdir(parents=True, exist_ok=True)

    try:
        bundle, bundle_metadata = collect_repo_bundle(repo, args.max_chars)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    write_text(out / "repo-context-bundle.txt", bundle)

    reader_prompt = build_reader_prompt(args.issue, bundle, bundle_metadata)
    write_text(out / "reader-prompt.txt", reader_prompt)

    try:
        reader_raw, reader_wall_seconds = call_ollama(args.reader, reader_prompt)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    reader_brief, reader_thinking = extract_message(reader_raw)
    reader_metrics = build_metrics(reader_raw, reader_wall_seconds, reader_brief)
    write_json(out / "reader-raw.json", reader_raw)
    write_text(out / "reader-brief.md", reader_brief)
    write_text(out / "reader-thinking.md", reader_thinking)
    write_json(out / "reader-metrics.json", reader_metrics)

    if reader_metrics["done_reason"] == "length" and len(reader_brief) < 500:
        message = (
            "Reader response ended because of length with very little output; "
            "the prompt likely filled the context window. Lower --max-chars or "
            "use a reader model with a larger context window."
        )
        write_text(out / "reader-error.txt", message + "\n")
        print(message, file=sys.stderr)
        return 1

    coder_prompt = build_coder_prompt(args.issue, reader_brief)
    write_text(out / "coder-prompt.txt", coder_prompt)

    try:
        coder_raw, coder_wall_seconds = call_ollama(args.coder, coder_prompt)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    coder_plan, coder_thinking = extract_message(coder_raw)
    coder_metrics = build_metrics(coder_raw, coder_wall_seconds, coder_plan)
    write_json(out / "coder-raw.json", coder_raw)
    write_text(out / "coder-plan.md", coder_plan)
    write_text(out / "coder-thinking.md", coder_thinking)
    write_json(out / "coder-metrics.json", coder_metrics)

    summary = {
        "repo": str(repo),
        "out": str(out),
        "reader": args.reader,
        "coder": args.coder,
        "issue_chars": len(args.issue),
        "bundle": bundle_metadata,
        "reader_metrics": reader_metrics,
        "coder_metrics": coder_metrics,
        "outputs": [
            "repo-context-bundle.txt",
            "reader-prompt.txt",
            "reader-brief.md",
            "reader-raw.json",
            "reader-thinking.md",
            "reader-metrics.json",
            "coder-prompt.txt",
            "coder-plan.md",
            "coder-raw.json",
            "coder-thinking.md",
            "coder-metrics.json",
            "summary.json",
        ],
    }
    write_json(out / "summary.json", summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
