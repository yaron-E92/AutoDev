from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TextIO


DEFAULT_READER = "qwen35-9b-32k"
DEFAULT_CODER = "devstral-small2-12k"
RUNNER_ROOT = Path(__file__).resolve().parents[1]
AREA_READER_SCRIPT = RUNNER_ROOT / "benchmarks" / "local-llm" / "area_reader_bench.py"
PROMPT_TEMPLATE_DIR = RUNNER_ROOT / "promptTemplates"
DEFAULT_VERIFY_TIMEOUT_SECONDS = 1800


@dataclass(frozen=True)
class CommandResult:
    argv: list[str]
    cwd: Path
    returncode: int
    stdout: str
    stderr: str


def main(argv: list[str] | None = None) -> int:
    return run(argv)


def run(
    argv: list[str] | None = None,
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    out_stream = stdout if stdout is not None else sys.stdout
    err_stream = stderr if stderr is not None else sys.stderr
    args = parse_args(argv)
    repo = expand_path(args.repo)
    out_dir = expand_path(args.out)

    try:
        validate_inputs(args, repo)
        require_tools(["gh", "git"])
        out_dir.mkdir(parents=True, exist_ok=True)

        issue_text = fetch_issue_text(args.github_repo, args.issue, repo, out_stream)
        write_text(out_dir / "issue.md", issue_text)

        if not args.allow_dirty:
            ensure_clean_worktree(repo, out_stream)

        branch_name = issue_branch_name(args.issue, issue_text)
        ensure_issue_branch(repo, branch_name, out_stream)

        area_out = out_dir / "area-reader-debug"
        run_area_reader(repo, issue_text, args.reader, args.coder, area_out, out_stream)
        write_operational_outputs(issue_text, area_out, out_dir, args.debug_artifacts)

        verification = run_recommended_verification(out_dir, repo, out_stream)
        write_text(out_dir / "verification-result-summary.md", verification)

        if args.mode == "plan-only":
            write_text(out_dir / "final-pr-summary.md", "Plan-only mode: no files were intentionally modified and no PR was opened.\n")
            print(f"Plan-only run complete. Outputs: {out_dir}", file=out_stream)
            return 0

        write_implementation_prompt(out_dir, args.mode)
        if args.mode == "implement":
            write_text(out_dir / "final-pr-summary.md", "Implement mode: implementation prompt prepared; no PR was opened.\n")
            print(f"Implement run complete. Outputs: {out_dir}", file=out_stream)
            return 0

        pr_summary = create_draft_pr(repo, args.github_repo, args.issue, issue_text, out_dir, out_stream)
        write_text(out_dir / "final-pr-summary.md", pr_summary)
        print(f"PR run complete. Outputs: {out_dir}", file=out_stream)
        return 0
    except RunnerError as exc:
        print(str(exc), file=err_stream)
        return exc.exit_code


class RunnerError(Exception):
    def __init__(self, message: str, exit_code: int = 1):
        super().__init__(message)
        self.exit_code = exit_code


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run an operational Codex issue-to-PR flow for one real GitHub issue."
    )
    parser.add_argument("--repo", required=True, help="Local repository path to operate on.")
    parser.add_argument("--github-repo", required=True, help="GitHub repository in owner/name form.")
    parser.add_argument("--issue", required=True, type=positive_int, help="GitHub issue number.")
    parser.add_argument("--reader", default=DEFAULT_READER, help=f"Ollama reader model. Default: {DEFAULT_READER}.")
    parser.add_argument("--coder", default=DEFAULT_CODER, help=f"Ollama coder model. Default: {DEFAULT_CODER}.")
    parser.add_argument("--mode", choices=("plan-only", "implement", "pr"), default="plan-only")
    parser.add_argument("--out", required=True, help="Output directory for concise run artifacts.")
    parser.add_argument("--debug-artifacts", action="store_true", help="Keep benchmark-style raw area-reader artifacts.")
    parser.add_argument("--allow-dirty", action="store_true", help="Allow running when the repo has uncommitted changes.")
    return parser.parse_args(argv)


def positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--issue must be an integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("--issue must be greater than zero")
    return parsed


def expand_path(value: str) -> Path:
    return Path(os.path.expanduser(value)).resolve()


def validate_inputs(args: argparse.Namespace, repo: Path) -> None:
    if not repo.is_dir():
        raise RunnerError(f"--repo is not a directory: {repo}", 2)
    if "/" not in args.github_repo or args.github_repo.count("/") != 1:
        raise RunnerError("--github-repo must use owner/name format", 2)
    if not AREA_READER_SCRIPT.is_file():
        raise RunnerError(f"Missing area-reader script: {AREA_READER_SCRIPT}", 2)


def require_tools(tools: list[str]) -> None:
    missing = [tool for tool in tools if shutil.which(tool) is None]
    if missing:
        raise RunnerError("Missing required executable(s): " + ", ".join(missing), 127)


def print_command(argv: list[str], cwd: Path, stream: TextIO) -> None:
    print(f"+ ({cwd}) {subprocess.list2cmdline(argv)}", file=stream)


def run_command(
    argv: list[str],
    *,
    cwd: Path,
    stream: TextIO,
    check: bool = True,
    timeout: int | None = None,
) -> CommandResult:
    print_command(argv, cwd, stream)
    completed = subprocess.run(
        argv,
        cwd=cwd,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    result = CommandResult(
        argv=argv,
        cwd=cwd,
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )
    if check and result.returncode != 0:
        details = "\n".join(
            part
            for part in (
                f"Command failed with exit code {result.returncode}: {subprocess.list2cmdline(argv)}",
                result.stdout.strip(),
                result.stderr.strip(),
            )
            if part
        )
        raise RunnerError(details)
    return result


def fetch_issue_text(github_repo: str, issue: int, repo: Path, stream: TextIO) -> str:
    result = run_command(
        [
            "gh",
            "issue",
            "view",
            str(issue),
            "--repo",
            github_repo,
            "--json",
            "title,body,url,labels",
        ],
        cwd=repo,
        stream=stream,
    )
    try:
        issue_data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RunnerError(f"gh issue view returned invalid JSON: {exc}") from exc

    title = str(issue_data.get("title") or "").strip()
    body = str(issue_data.get("body") or "").strip()
    url = str(issue_data.get("url") or "").strip()
    labels = issue_data.get("labels") or []
    label_names = [
        str(label.get("name"))
        for label in labels
        if isinstance(label, dict) and label.get("name")
    ]
    return "\n".join(
        [
            f"# GitHub Issue #{issue}: {title}",
            "",
            f"URL: {url}",
            "",
            f"Repository: {github_repo}",
            "",
            "Labels: " + (", ".join(label_names) if label_names else "(none)"),
            "",
            body,
            "",
        ]
    )


def ensure_clean_worktree(repo: Path, stream: TextIO) -> None:
    result = run_command(["git", "status", "--porcelain"], cwd=repo, stream=stream)
    if result.stdout.strip():
        raise RunnerError(
            "Refusing to run with uncommitted changes. Commit, stash, or pass --allow-dirty.",
            2,
        )


def issue_branch_name(issue: int, issue_text: str) -> str:
    title_line = next(
        (line for line in issue_text.splitlines() if line.startswith(f"# GitHub Issue #{issue}:")),
        "",
    )
    title = title_line.split(":", 1)[1] if ":" in title_line else f"issue-{issue}"
    slug = re.sub(r"[^a-z0-9]+", "-", title.casefold()).strip("-")
    if not slug:
        slug = "real-issue"
    return f"codex/issue-{issue}-{slug[:60]}"


def ensure_issue_branch(repo: Path, branch_name: str, stream: TextIO) -> None:
    current = run_command(["git", "branch", "--show-current"], cwd=repo, stream=stream).stdout.strip()
    if current == branch_name:
        return
    if current in {"main", "master"}:
        run_command(["git", "switch", "-c", branch_name], cwd=repo, stream=stream)
    elif current.startswith("codex/"):
        run_command(["git", "switch", "-c", branch_name], cwd=repo, stream=stream)
    else:
        raise RunnerError(
            f"Refusing to branch from unexpected current branch '{current}'. "
            "Start from main or an existing codex branch.",
            2,
        )


def run_area_reader(repo: Path, issue_text: str, reader: str, coder: str, out_dir: Path, stream: TextIO) -> None:
    if out_dir.exists():
        shutil.rmtree(out_dir)
    command = [
        sys.executable,
        str(AREA_READER_SCRIPT),
        "--repo",
        str(repo),
        "--reader",
        reader,
        "--coder",
        coder,
        "--issue",
        issue_text,
        "--out",
        str(out_dir),
    ]
    run_command(command, cwd=RUNNER_ROOT, stream=stream)


def write_operational_outputs(issue_text: str, area_out: Path, out_dir: Path, keep_debug: bool) -> None:
    copies = {
        "routing.json": "routed-areas.json",
        "synthesis-brief.md": "synthesized-handoff.md",
        "coder-plan.md": "coder-plan.md",
        "recommended-command-groups.json": "recommended-command-groups.json",
        "verification-commands.sh": "verification-commands.sh",
    }
    write_text(out_dir / "issue.md", issue_text)
    for source_name, target_name in copies.items():
        source = area_out / source_name
        target = out_dir / target_name
        if source.is_file():
            shutil.copyfile(source, target)

    write_text(out_dir / "run-summary.md", build_run_summary(out_dir))
    if not keep_debug:
        shutil.rmtree(area_out, ignore_errors=True)


def build_run_summary(out_dir: Path) -> str:
    routing = read_json(out_dir / "routed-areas.json")
    recommendations = read_json(out_dir / "recommended-command-groups.json")
    areas = routing.get("areas", []) if isinstance(routing, dict) else []
    groups = recommendations.get("recommended_command_groups", []) if isinstance(recommendations, dict) else []
    lines = [
        "# Real-Issue Run Summary",
        "",
        "Routed areas: " + (", ".join(str(area) for area in areas) if areas else "(none recorded)"),
        "Recommended verification groups: "
        + (", ".join(str(group) for group in groups) if groups else "(none recorded)"),
        "",
        "Primary outputs:",
        "",
        "- issue.md",
        "- routed-areas.json",
        "- synthesized-handoff.md",
        "- coder-plan.md",
        "- recommended-command-groups.json",
        "- verification-result-summary.md",
        "- final-pr-summary.md",
        "",
    ]
    return "\n".join(lines)


def read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def run_recommended_verification(out_dir: Path, repo: Path, stream: TextIO) -> str:
    script = out_dir / "verification-commands.sh"
    if not script.is_file():
        script = out_dir / "area-reader-debug" / "verification-commands.sh"
    if not script.is_file():
        return "Verification was not run because the area-reader verification script was not available.\n"

    result = run_command(
        ["bash", str(script), "recommended"],
        cwd=repo,
        stream=stream,
        check=False,
        timeout=DEFAULT_VERIFY_TIMEOUT_SECONDS,
    )
    summary = [
        "# Verification Result Summary",
        "",
        f"Exit code: {result.returncode}",
        "",
        "## Command",
        "",
        subprocess.list2cmdline(result.argv),
        "",
        "## Output",
        "",
        trim_log(result.stdout),
    ]
    if result.stderr.strip():
        summary.extend(["", "## Error Output", "", trim_log(result.stderr)])
    summary.append("")
    if result.returncode != 0:
        raise RunnerError("\n".join(summary))
    return "\n".join(summary)


def trim_log(value: str, limit: int = 12000) -> str:
    if len(value) <= limit:
        return value.rstrip() + "\n"
    return value[-limit:].rstrip() + "\n"


def write_implementation_prompt(out_dir: Path, mode: str) -> None:
    existing_prompt = read_prompt_template("implementer.md")
    handoff = read_optional_text(out_dir / "synthesized-handoff.md")
    coder_plan = read_optional_text(out_dir / "coder-plan.md")
    text = "\n".join(
        [
            "# Local Implementation Prompt",
            "",
            f"Mode: {mode}",
            "",
            "Use the existing Codex automation behavior as the operating baseline:",
            "",
            existing_prompt.strip(),
            "",
            "## Synthesized handoff",
            "",
            handoff.strip(),
            "",
            "## Coder plan",
            "",
            coder_plan.strip(),
            "",
        ]
    )
    write_text(out_dir / "implementation-prompt.md", text)


def read_prompt_template(name: str) -> str:
    return read_optional_text(PROMPT_TEMPLATE_DIR / name)


def read_optional_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def create_draft_pr(repo: Path, github_repo: str, issue: int, issue_text: str, out_dir: Path, stream: TextIO) -> str:
    current_branch = run_command(["git", "branch", "--show-current"], cwd=repo, stream=stream).stdout.strip()
    if current_branch in {"main", "master"}:
        raise RunnerError("Refusing to create a PR from the main branch.", 2)

    status = run_command(["git", "status", "--porcelain"], cwd=repo, stream=stream).stdout.strip()
    if not status:
        raise RunnerError("No changes detected for PR mode after verification.", 2)

    title = first_issue_title(issue_text) or f"Issue #{issue}"
    body_path = out_dir / "draft-pr-body.md"
    write_text(
        body_path,
        "\n".join(
            [
                f"Closes #{issue}",
                "",
                "## Summary",
                "",
                read_optional_text(out_dir / "coder-plan.md").strip() or "Prepared by the real-issue runner.",
                "",
                "## Verification",
                "",
                read_optional_text(out_dir / "verification-result-summary.md").strip(),
                "",
            ]
        ),
    )
    run_command(["git", "add", "--all"], cwd=repo, stream=stream)
    run_command(["git", "commit", "-m", f"Implement issue {issue} real-issue runner changes"], cwd=repo, stream=stream)
    run_command(["git", "push", "-u", "origin", current_branch], cwd=repo, stream=stream)
    result = run_command(
        [
            "gh",
            "pr",
            "create",
            "--repo",
            github_repo,
            "--draft",
            "--title",
            title,
            "--body-file",
            str(body_path),
            "--base",
            "main",
            "--head",
            current_branch,
        ],
        cwd=repo,
        stream=stream,
    )
    return "Draft PR created:\n\n" + result.stdout.strip() + "\n"


def first_issue_title(issue_text: str) -> str:
    for line in issue_text.splitlines():
        if line.startswith("# GitHub Issue") and ":" in line:
            return line.split(":", 1)[1].strip()
    return ""


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
