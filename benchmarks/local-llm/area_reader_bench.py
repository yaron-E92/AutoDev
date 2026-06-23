#!/usr/bin/env python3
"""Run an area-based local Ollama reader-synthesis-coder benchmark."""

import argparse
import fnmatch
import json
import os
from pathlib import Path
import sys
import time
from urllib import error, request


OLLAMA_CHAT_URL = "http://localhost:11434/api/chat"
DEFAULT_MAX_CHARS_PER_AREA = 50000
DEFAULT_READER_NUM_PREDICT = 1800
DEFAULT_SYNTH_NUM_PREDICT = 2200
DEFAULT_CODER_NUM_PREDICT = 2200
MAX_FILE_BYTES = 250000

SUPPORTED_AREAS = ("backend", "web", "maui", "ci", "tests", "docs", "api-client")
DEFAULT_AUTO_AREAS = ("backend", "web", "maui", "ci")

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
    ".py",
    ".toml",
    ".lock",
}

INCLUDED_FILENAMES = {
    "AGENTS.md",
    "README.md",
    "CONTRIBUTING.md",
    "Dockerfile",
    "Makefile",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "bun.lockb",
    "Pipfile.lock",
    "poetry.lock",
    "Directory.Build.props",
    "Directory.Packages.props",
    "MauiProgram.cs",
    "App.xaml",
    "Program.cs",
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
    ".benchmark-results",
    "__pycache__",
    "TestResults",
    "dist",
    "build",
    "coverage",
}

PRIORITY_PATTERNS = (
    "AGENTS.md",
    "README.md",
    ".github/workflows/*.yml",
    ".github/workflows/*.yaml",
    "*.sln",
    "*.csproj",
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "bun.lockb",
    "tsconfig*",
    "vite.config*",
    "MauiProgram.cs",
    "App.xaml",
    "Program.cs",
    "Directory.Build.props",
    "Directory.Packages.props",
    "docs/*",
    "doc/*",
    "adr/*",
    "ADRs/*",
)

AREA_HINTS = {
    "backend": {
        "keywords": ("backend", "server", "api", "database", "db", "ef", "migration"),
        "path_patterns": (
            "*backend*",
            "*server*",
            "*api*",
            "*.sln",
            "*.csproj",
            "Program.cs",
            "Directory.Build.props",
            "Directory.Packages.props",
        ),
    },
    "web": {
        "keywords": ("web", "frontend", "react", "vite", "typescript", "browser", "ui"),
        "path_patterns": (
            "*web*",
            "*frontend*",
            "*react*",
            "package.json",
            "package-lock.json",
            "pnpm-lock.yaml",
            "yarn.lock",
            "tsconfig*",
            "vite.config*",
            "*.ts",
            "*.tsx",
            "*.js",
            "*.jsx",
            "*.css",
            "*.html",
        ),
    },
    "maui": {
        "keywords": ("maui", "mobile", "desktop", "android", "ios", "xaml"),
        "path_patterns": (
            "*maui*",
            "*mobile*",
            "*android*",
            "*ios*",
            "*.xaml",
            "MauiProgram.cs",
            "App.xaml",
        ),
    },
    "ci": {
        "keywords": ("ci", "workflow", "github actions", "build", "verify", "pipeline"),
        "path_patterns": (
            ".github/workflows/*.yml",
            ".github/workflows/*.yaml",
            "*workflow*",
            "*ci*",
            "*.sh",
            "*.ps1",
            "codex-profiles.json",
        ),
    },
    "tests": {
        "keywords": ("test", "tests", "verification", "xunit", "pytest", "playwright"),
        "path_patterns": (
            "*test*",
            "*tests*",
            "*.Tests/*",
            "*.Test/*",
            "pytest.ini",
            "playwright.config*",
        ),
    },
    "docs": {
        "keywords": ("docs", "documentation", "readme", "adr", "guide"),
        "path_patterns": (
            "README.md",
            "CONTRIBUTING.md",
            "docs/*",
            "doc/*",
            "adr/*",
            "ADRs/*",
            "*.md",
        ),
    },
    "api-client": {
        "keywords": ("api client", "client", "sdk", "http client", "openapi"),
        "path_patterns": (
            "*client*",
            "*sdk*",
            "*openapi*",
            "*swagger*",
            "*.ts",
            "*.tsx",
            "*.cs",
        ),
    },
}


def parse_args():
    parser = argparse.ArgumentParser(
        description="Run an area-based local Ollama reader-synthesis-coder benchmark."
    )
    parser.add_argument("--repo", required=True, help="Repository to read for context.")
    parser.add_argument("--reader", required=True, help="Ollama area reader model name.")
    parser.add_argument(
        "--synthesizer",
        help="Ollama synthesis reader model name. Defaults to --reader.",
    )
    parser.add_argument("--coder", required=True, help="Ollama coder model name.")
    parser.add_argument("--issue", required=True, help="Issue or task text.")
    parser.add_argument(
        "--areas",
        default="auto",
        help="Area routing: auto, all, or comma-list such as backend,web,maui,ci.",
    )
    parser.add_argument(
        "--max-chars-per-area",
        type=int,
        default=DEFAULT_MAX_CHARS_PER_AREA,
        help=f"Maximum input bundle characters per area. Default: {DEFAULT_MAX_CHARS_PER_AREA}.",
    )
    parser.add_argument(
        "--reader-num-predict",
        type=int,
        default=DEFAULT_READER_NUM_PREDICT,
        help=f"Area reader num_predict option. Default: {DEFAULT_READER_NUM_PREDICT}.",
    )
    parser.add_argument(
        "--synth-num-predict",
        type=int,
        default=DEFAULT_SYNTH_NUM_PREDICT,
        help=f"Synthesis reader num_predict option. Default: {DEFAULT_SYNTH_NUM_PREDICT}.",
    )
    parser.add_argument(
        "--coder-num-predict",
        type=int,
        default=DEFAULT_CODER_NUM_PREDICT,
        help=f"Coder num_predict option. Default: {DEFAULT_CODER_NUM_PREDICT}.",
    )
    parser.add_argument("--out", required=True, help="Output directory for benchmark files.")
    return parser.parse_args()


def expand_user_path(value):
    return Path(os.path.expanduser(value)).resolve()


def write_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path, value):
    write_text(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


def is_included_file(path):
    return path.name in INCLUDED_FILENAMES or path.suffix in INCLUDED_SUFFIXES


def iter_candidate_files(repo):
    for root, dirnames, filenames in os.walk(repo):
        dirnames[:] = sorted(name for name in dirnames if name not in EXCLUDED_DIRS)
        for filename in sorted(filenames):
            path = Path(root) / filename
            if is_included_file(path):
                yield path


def matches_any(path_text, patterns):
    return any(fnmatch.fnmatch(path_text, pattern) for pattern in patterns)


def is_priority_file(relative_path):
    return matches_any(relative_path, PRIORITY_PATTERNS) or Path(relative_path).name in INCLUDED_FILENAMES


def area_for_file(relative_path, area):
    hints = AREA_HINTS[area]
    lowered = relative_path.lower()
    return matches_any(relative_path, hints["path_patterns"]) or any(
        keyword in lowered for keyword in hints["keywords"]
    )


def collect_repo_files(repo):
    files = []
    skipped_large_files = []
    skipped_unreadable_files = []

    for path in iter_candidate_files(repo):
        try:
            size = path.stat().st_size
        except OSError as exc:
            skipped_unreadable_files.append({"path": str(path), "reason": str(exc)})
            continue

        relative_path = path.relative_to(repo).as_posix()
        if size > MAX_FILE_BYTES:
            skipped_large_files.append({"path": relative_path, "bytes": size})
            continue

        areas = [area for area in SUPPORTED_AREAS if area_for_file(relative_path, area)]
        files.append(
            {
                "path": relative_path,
                "bytes": size,
                "priority": is_priority_file(relative_path),
                "areas": areas,
            }
        )

    files.sort(key=lambda item: (not item["priority"], item["path"]))
    return files, skipped_large_files, skipped_unreadable_files


def build_repo_map(repo, files, skipped_large_files, skipped_unreadable_files):
    lines = [
        f"Repository: {repo}",
        "",
        "Candidate files:",
    ]
    for item in files:
        flags = []
        if item["priority"]:
            flags.append("priority")
        if item["areas"]:
            flags.append("areas=" + ",".join(item["areas"]))
        suffix = " [" + "; ".join(flags) + "]" if flags else ""
        lines.append(f"- {item['path']} ({item['bytes']} bytes){suffix}")

    if skipped_large_files:
        lines.extend(["", "Skipped large files:"])
        for item in skipped_large_files:
            lines.append(f"- {item['path']} ({item['bytes']} bytes)")

    if skipped_unreadable_files:
        lines.extend(["", "Skipped unreadable files:"])
        for item in skipped_unreadable_files:
            lines.append(f"- {item['path']}: {item['reason']}")

    return "\n".join(lines) + "\n"


def route_areas(issue, areas_arg):
    requested = areas_arg.strip()
    if requested == "all":
        return list(SUPPORTED_AREAS), {
            "mode": "all",
            "matched_keywords": {},
            "defaulted": False,
        }

    if requested != "auto":
        areas = []
        for raw_area in requested.split(","):
            area = raw_area.strip()
            if not area:
                continue
            if area not in SUPPORTED_AREAS:
                raise ValueError(f"Unsupported area: {area}")
            if area not in areas:
                areas.append(area)
        if not areas:
            raise ValueError("--areas explicit list did not include any supported areas")
        return areas, {
            "mode": "explicit",
            "matched_keywords": {},
            "defaulted": False,
        }

    issue_lower = issue.lower()
    matched_keywords = {}
    areas = []
    for area in SUPPORTED_AREAS:
        keywords = [keyword for keyword in AREA_HINTS[area]["keywords"] if keyword in issue_lower]
        if keywords:
            areas.append(area)
            matched_keywords[area] = keywords

    defaulted = not areas
    if defaulted:
        areas = list(DEFAULT_AUTO_AREAS)

    return areas, {
        "mode": "auto",
        "matched_keywords": matched_keywords,
        "defaulted": defaulted,
    }


def area_file_map(files, area):
    selected = [
        item
        for item in files
        if area in item["areas"] or item["priority"]
    ]
    selected.sort(key=lambda item: (area not in item["areas"], not item["priority"], item["path"]))
    return selected


def format_area_file_map(area, files):
    lines = [f"Area file map: {area}"]
    if not files:
        lines.append("- No candidate files matched this area.")
    for item in files:
        flags = []
        if area in item["areas"]:
            flags.append("area-match")
        if item["priority"]:
            flags.append("priority")
        suffix = " [" + "; ".join(flags) + "]" if flags else ""
        lines.append(f"- {item['path']} ({item['bytes']} bytes){suffix}")
    return "\n".join(lines) + "\n"


def read_file_for_bundle(repo, relative_path):
    path = repo / relative_path
    return path.read_text(encoding="utf-8", errors="replace")


def build_area_bundle(repo, area, issue, repo_map, files, max_chars):
    if max_chars <= 0:
        raise ValueError("--max-chars-per-area must be greater than zero")

    file_map_text = format_area_file_map(area, files)
    header = f"""Issue:
{issue}

Routed area: {area}

Area hint keywords:
{", ".join(AREA_HINTS[area]["keywords"])}

{file_map_text}
Repository map:
{repo_map}
File excerpts:
"""
    parts = [header]
    remaining = max_chars - len(header)
    included_files = []
    skipped_unreadable_files = []
    truncated = remaining < 0

    if remaining > 0:
        for item in files:
            relative_path = item["path"]
            try:
                content = read_file_for_bundle(repo, relative_path)
            except OSError as exc:
                skipped_unreadable_files.append({"path": relative_path, "reason": str(exc)})
                continue

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

    bundle = "".join(parts)
    metadata = {
        "area": area,
        "max_chars": max_chars,
        "bundle_chars": len(bundle),
        "candidate_file_count": len(files),
        "included_file_count": len(included_files),
        "included_files": included_files,
        "skipped_unreadable_files": skipped_unreadable_files,
        "truncated": truncated,
        "placeholder_or_absent": not any(area in item["areas"] for item in files),
    }
    return bundle, metadata, file_map_text


def build_area_reader_prompt(issue, area, bundle, metadata):
    return f"""You are the area reader model for area: {area}.

You are not the coder. Do not edit files. Do not design a patch. Read only the provided repository context and produce a factual handoff brief for a later synthesis reader and coder.

Your brief must:
- Include exact file paths for every repository fact you mention.
- Distinguish visible facts from inference.
- Include verification commands for this area only.
- Make commands runnable from the repository root unless you explicitly state a required cd.
- Identify uncertainties and missing files.
- If this area is placeholder-only or not actually present, say so clearly.

Original issue:
{issue}

Area bundle metadata:
{json.dumps(metadata, indent=2, sort_keys=True)}

Area input bundle:
{bundle}
"""


def build_synthesis_prompt(issue, areas, area_results):
    brief_blocks = []
    for result in area_results:
        brief_blocks.append(
            f"""## Area: {result['area']}

Reader metadata:
{json.dumps(result['metadata'], indent=2, sort_keys=True)}

Reader brief:
{result['brief']}
"""
        )

    return f"""You are the synthesis reader model in an area-based local LLM benchmark.

You are not the coder. Combine the area reader briefs into one compact coder handoff.

Your handoff must:
- Preserve area-specific details.
- List routed areas.
- List repo/application surfaces.
- List relevant files by area.
- Include repo-root verification commands.
- Include cross-area risks.
- Include constraints and uncertainties.
- Do not invent files or commands.

Original issue:
{issue}

Routed areas:
{", ".join(areas)}

Area reader briefs:
{"".join(brief_blocks)}
"""


def build_coder_prompt(issue, synthesis_brief):
    return f"""You are the coder model in an area-based local LLM benchmark.

Consume the original issue and the synthesized handoff. Produce a minimal issue-scoped implementation or verification plan.

Rules:
- For verification-only issues, list "files to inspect," not "files likely needing changes."
- Name exact files only when supported by the handoff.
- Commands must be runnable from the repository root unless an explicit cd command is included.
- Do not use placeholder commands.
- Do not invent test projects or paths.
- Do not refactor unrelated code.
- Be strict about uncertainty.

Original issue:
{issue}

Synthesized handoff:
{synthesis_brief}
"""


def call_ollama(model, prompt, num_predict):
    payload = {
        "model": model,
        "stream": False,
        "think": False,
        "options": {
            "num_predict": num_predict,
        },
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


def run_area_reader(args, repo, out, area, repo_map, files):
    area_dir = out / f"area-{area}"
    selected_files = area_file_map(files, area)
    bundle, metadata, file_map_text = build_area_bundle(
        repo,
        area,
        args.issue,
        repo_map,
        selected_files,
        args.max_chars_per_area,
    )
    reader_prompt = build_area_reader_prompt(args.issue, area, bundle, metadata)

    write_text(area_dir / "file-map.txt", file_map_text)
    write_text(area_dir / "input-bundle.txt", bundle)
    write_text(area_dir / "reader-prompt.txt", reader_prompt)

    raw, wall_seconds = call_ollama(args.reader, reader_prompt, args.reader_num_predict)
    brief, thinking = extract_message(raw)
    metrics = build_metrics(raw, wall_seconds, brief)

    write_json(area_dir / "reader-raw.json", raw)
    write_text(area_dir / "reader-brief.md", brief)
    write_text(area_dir / "reader-thinking.md", thinking)
    write_json(area_dir / "metrics.json", metrics)

    reader_error = ""
    if metrics["done_reason"] == "length" and len(brief) < 500:
        reader_error = (
            "Area reader response ended because of length with very little output; "
            "the prompt likely filled the context window. Lower --max-chars-per-area "
            "or use a reader model with a larger context window."
        )
        write_text(area_dir / "reader-error.txt", reader_error + "\n")
        print(f"{area}: {reader_error}", file=sys.stderr)

    return {
        "area": area,
        "brief": brief,
        "metrics": metrics,
        "metadata": metadata,
        "reader_error": reader_error,
    }


def main():
    args = parse_args()
    if args.synthesizer is None:
        args.synthesizer = args.reader

    repo = expand_user_path(args.repo)
    out = expand_user_path(args.out)

    if not repo.is_dir():
        print(f"--repo is not a directory: {repo}", file=sys.stderr)
        return 2

    try:
        areas, routing_detail = route_areas(args.issue, args.areas)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if args.max_chars_per_area <= 0:
        print("--max-chars-per-area must be greater than zero", file=sys.stderr)
        return 2

    out.mkdir(parents=True, exist_ok=True)
    write_text(out / "issue.txt", args.issue + "\n")

    files, skipped_large_files, skipped_unreadable_files = collect_repo_files(repo)
    repo_map = build_repo_map(repo, files, skipped_large_files, skipped_unreadable_files)
    write_text(out / "repo-map.txt", repo_map)

    routing = {
        "requested": args.areas,
        "areas": areas,
        "supported_areas": list(SUPPORTED_AREAS),
        "detail": routing_detail,
    }
    write_json(out / "routing.json", routing)

    area_results = []
    for area in areas:
        try:
            area_results.append(run_area_reader(args, repo, out, area, repo_map, files))
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return 1

    synthesis_prompt = build_synthesis_prompt(args.issue, areas, area_results)
    write_text(out / "synthesis-prompt.txt", synthesis_prompt)
    try:
        synthesis_raw, synthesis_wall_seconds = call_ollama(
            args.synthesizer,
            synthesis_prompt,
            args.synth_num_predict,
        )
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    synthesis_brief, synthesis_thinking = extract_message(synthesis_raw)
    synthesis_metrics = build_metrics(synthesis_raw, synthesis_wall_seconds, synthesis_brief)
    write_json(out / "synthesis-raw.json", synthesis_raw)
    write_text(out / "synthesis-brief.md", synthesis_brief)
    write_text(out / "synthesis-thinking.md", synthesis_thinking)
    write_json(out / "synthesis-metrics.json", synthesis_metrics)

    coder_prompt = build_coder_prompt(args.issue, synthesis_brief)
    write_text(out / "coder-prompt.txt", coder_prompt)
    try:
        coder_raw, coder_wall_seconds = call_ollama(
            args.coder,
            coder_prompt,
            args.coder_num_predict,
        )
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    coder_plan, coder_thinking = extract_message(coder_raw)
    coder_metrics = build_metrics(coder_raw, coder_wall_seconds, coder_plan)
    write_json(out / "coder-raw.json", coder_raw)
    write_text(out / "coder-plan.md", coder_plan)
    write_text(out / "coder-thinking.md", coder_thinking)
    write_json(out / "coder-metrics.json", coder_metrics)

    area_metrics = {result["area"]: result["metrics"] for result in area_results}
    summary = {
        "repo": str(repo),
        "out": str(out),
        "reader": args.reader,
        "synthesizer": args.synthesizer,
        "coder": args.coder,
        "max_chars_per_area": args.max_chars_per_area,
        "areas": areas,
        "routing": routing,
        "repo_file_count": len(files),
        "skipped_large_files": skipped_large_files,
        "skipped_unreadable_files": skipped_unreadable_files,
        "area_metadata": {result["area"]: result["metadata"] for result in area_results},
        "area_metrics": area_metrics,
        "area_errors": {
            result["area"]: result["reader_error"]
            for result in area_results
            if result["reader_error"]
        },
        "synthesis_metrics": synthesis_metrics,
        "coder_metrics": coder_metrics,
        "outputs": [
            "issue.txt",
            "repo-map.txt",
            "routing.json",
            *[
                f"area-{area}/{name}"
                for area in areas
                for name in (
                    "file-map.txt",
                    "input-bundle.txt",
                    "reader-prompt.txt",
                    "reader-brief.md",
                    "reader-raw.json",
                    "reader-thinking.md",
                    "metrics.json",
                )
            ],
            "synthesis-prompt.txt",
            "synthesis-brief.md",
            "synthesis-raw.json",
            "synthesis-thinking.md",
            "synthesis-metrics.json",
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
