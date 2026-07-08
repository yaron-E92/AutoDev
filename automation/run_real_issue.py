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
from typing import Callable, TextIO

from area_reader_v2 import runner as area_reader_runner
from automation.model_providers import (
    ModelConfig,
    ModelProvider,
    ProviderError,
    create_provider,
    load_provider_config,
    ollama_command_for_model,
    resolve_model_config,
)


DEFAULT_READER_MODEL = "qwen35-9b-32k"
DEFAULT_CODER_MODEL = "devstral-small2-12k"
DEFAULT_READY_LABEL = "autodev:ready"
DEFAULT_RUNNING_LABEL = "autodev:running"
DEFAULT_FAILED_LABEL = "autodev:failed"
DEFAULT_DONE_LABEL = "autodev:done"
DEFAULT_BLOCKED_LABEL = "autodev:blocked"
RUNNER_ROOT = Path(__file__).resolve().parents[1]
PROMPT_TEMPLATE_DIR = RUNNER_ROOT / "promptTemplates"
PATCH_START = "BEGIN_UNIFIED_DIFF"
PATCH_END = "END_UNIFIED_DIFF"
NO_CHANGES_REQUIRED = "NO_CHANGES_REQUIRED"


@dataclass(frozen=True)
class CommandResult:
    argv: list[str]
    cwd: Path
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class IssueSelection:
    number: int
    title: str
    url: str
    labels: list[str]
    body: str = ""


@dataclass(frozen=True)
class VerificationResult:
    attempt: int
    returncode: int
    command_group: str
    stdout: str
    stderr: str
    summary_path: Path

    @property
    def passed(self) -> bool:
        return self.returncode == 0


class RunnerError(Exception):
    def __init__(self, message: str, exit_code: int = 1):
        super().__init__(message)
        self.exit_code = exit_code


def main(argv: list[str] | None = None) -> int:
    return run(argv)


def run(
    argv: list[str] | None = None,
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    provider_factory: Callable[[ModelConfig], ModelProvider] | None = None,
) -> int:
    out_stream = stdout if stdout is not None else sys.stdout
    err_stream = stderr if stderr is not None else sys.stderr
    args = parse_args(argv)
    repo = expand_path(args.repo)
    out_dir = expand_path(args.out)

    labels_started = False
    issue_number = args.issue
    try:
        validate_inputs(args, repo)
        require_tools(["gh", "git"])
        out_dir.mkdir(parents=True, exist_ok=True)

        reader_config, coder_config = resolve_provider_configs(args)
        provider_factory = provider_factory or create_provider
        reader_provider = provider_factory(reader_config)
        coder_provider = provider_factory(coder_config)
        write_provider_metadata(out_dir, reader_config, coder_config)

        selected = select_issue(args, repo, out_stream)
        issue_number = selected.number
        write_json(out_dir / "selected-issue.json", selected.__dict__)
        issue_text = fetch_issue_text(args.github_repo, selected.number, repo, out_stream)
        write_text(out_dir / "issue.md", issue_text)

        if args.manage_labels or args.next:
            update_issue_labels(
                repo,
                args.github_repo,
                selected.number,
                add=[args.running_label],
                remove=[],
                stream=out_stream,
            )
            labels_started = True

        if not args.allow_dirty:
            ensure_clean_worktree(repo, out_stream)

        branch_name = issue_branch_name(selected.number, issue_text)
        ensure_issue_branch(repo, branch_name, out_stream)

        area_out = out_dir / "area-reader-debug"
        run_area_reader(repo, issue_text, reader_config, coder_config, area_out, out_stream)
        write_operational_outputs(issue_text, area_out, out_dir, args.debug_artifacts)

        if args.mode == "plan-only":
            write_implementation_prompt_file(out_dir, issue_text, branch_name)
            if args.baseline_verify:
                verification = run_recommended_verification(out_dir, repo, 0, out_stream)
                write_text(out_dir / "verification-result-summary.md", render_verification_summary(verification))
            else:
                write_text(out_dir / "verification-result-summary.md", "Baseline verification was skipped.\n")
            write_text(out_dir / "final-pr-summary.md", "Plan-only mode completed without coder execution.\n")
            print(f"Plan-only run complete. Outputs: {out_dir}", file=out_stream)
            return 0

        if args.skip_implementation:
            write_implementation_prompt_file(out_dir, issue_text, branch_name)
            write_text(out_dir / "verification-result-summary.md", "Implementation skipped; verification was not run.\n")
            write_text(out_dir / "final-pr-summary.md", "Skipped implementation. No PR was opened.\n")
            print(f"Implementation skipped. Outputs: {out_dir}", file=out_stream)
            return 0

        result = run_implementation_loop(
            repo=repo,
            out_dir=out_dir,
            issue_text=issue_text,
            branch_name=branch_name,
            coder_provider=coder_provider,
            coder_config=coder_config,
            max_fix_attempts=args.max_fix_attempts,
            dry_run=args.dry_run_implementation,
            stream=out_stream,
        )
        if args.dry_run_implementation:
            write_text(out_dir / "final-pr-summary.md", "Dry-run implementation completed; patch was not applied.\n")
            print(f"Dry-run implementation complete. Outputs: {out_dir}", file=out_stream)
            return 0
        if not result.passed:
            raise RunnerError("verification failed after fix attempts")

        if args.mode == "implement":
            write_text(out_dir / "final-pr-summary.md", "Implement mode completed with verified working-tree changes.\n")
            print(f"Implement run complete. Outputs: {out_dir}", file=out_stream)
            return 0

        pr_summary = create_draft_pr(
            repo,
            args.github_repo,
            selected.number,
            issue_text,
            out_dir,
            reader_config,
            coder_config,
            out_stream,
        )
        write_text(out_dir / "final-pr-summary.md", pr_summary)
        if labels_started:
            update_issue_labels(
                repo,
                args.github_repo,
                selected.number,
                add=[args.done_label],
                remove=[args.running_label],
                stream=out_stream,
            )
        print(f"PR run complete. Outputs: {out_dir}", file=out_stream)
        return 0
    except (RunnerError, ProviderError) as exc:
        if labels_started and issue_number:
            try:
                update_issue_labels(
                    repo,
                    args.github_repo,
                    issue_number,
                    add=[args.failed_label],
                    remove=[args.running_label],
                    stream=out_stream,
                )
            except Exception as label_exc:  # pragma: no cover - best effort label cleanup
                print(f"label cleanup failed: {label_exc}", file=err_stream)
        print(str(exc), file=err_stream)
        return exc.exit_code if isinstance(exc, RunnerError) else 1


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run an AutoDev issue-to-PR flow with provider-agnostic reader and coder models."
    )
    parser.add_argument("--repo", required=True, help="Local repository path to operate on.")
    parser.add_argument("--github-repo", required=True, help="GitHub repository in owner/name form.")
    issue_group = parser.add_mutually_exclusive_group(required=True)
    issue_group.add_argument("--issue", type=positive_int, help="GitHub issue number.")
    issue_group.add_argument("--next", action="store_true", help="Select the next eligible issue.")
    parser.add_argument("--mode", choices=("plan-only", "implement", "pr"), default="plan-only")
    parser.add_argument("--out", required=True, help="Output directory for concise run artifacts.")
    parser.add_argument("--debug-artifacts", action="store_true", help="Keep benchmark-style raw area-reader artifacts.")
    parser.add_argument("--allow-dirty", action="store_true", help="Allow running when the repo has uncommitted changes.")
    parser.add_argument("--provider-config", help="Optional JSON provider configuration file.")

    add_provider_args(parser, "reader", DEFAULT_READER_MODEL)
    add_provider_args(parser, "coder", DEFAULT_CODER_MODEL)

    parser.add_argument("--max-fix-attempts", type=non_negative_int, default=2)
    parser.add_argument("--skip-implementation", action="store_true")
    parser.add_argument("--dry-run-implementation", action="store_true")
    parser.add_argument("--baseline-verify", action="store_true")

    parser.add_argument("--ready-label", default=DEFAULT_READY_LABEL)
    parser.add_argument("--running-label", default=DEFAULT_RUNNING_LABEL)
    parser.add_argument("--failed-label", default=DEFAULT_FAILED_LABEL)
    parser.add_argument("--done-label", default=DEFAULT_DONE_LABEL)
    parser.add_argument("--blocked-label", default=DEFAULT_BLOCKED_LABEL)
    parser.add_argument("--limit", type=positive_int, default=25)
    parser.add_argument("--selection", choices=("oldest", "newest"), default="oldest")
    parser.add_argument("--manage-labels", action="store_true")
    return parser.parse_args(argv)


def add_provider_args(parser: argparse.ArgumentParser, role: str, default_model: str) -> None:
    parser.add_argument(f"--{role}-provider", choices=("command", "chat-completions", "openai-compatible", "mock"))
    parser.add_argument(f"--{role}-command")
    parser.add_argument(f"--{role}-base-url")
    parser.add_argument(f"--{role}-model", default=None, help=f"{role.title()} model name. Default: {default_model}.")
    parser.add_argument(f"--{role}-api-key-env")
    parser.add_argument(f"--{role}-timeout-seconds", type=positive_int)
    legacy = "--reader" if role == "reader" else "--coder"
    parser.add_argument(legacy, dest=f"{role}_model", help=argparse.SUPPRESS)


def positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("value must be an integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be greater than zero")
    return parsed


def non_negative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("value must be zero or greater")
    return parsed


def expand_path(value: str) -> Path:
    return Path(os.path.expanduser(value)).resolve()


def validate_inputs(args: argparse.Namespace, repo: Path) -> None:
    if not repo.is_dir():
        raise RunnerError(f"--repo is not a directory: {repo}", 2)
    if "/" not in args.github_repo or args.github_repo.count("/") != 1:
        raise RunnerError("--github-repo must use owner/name format", 2)
    if not Path(area_reader_runner.__file__).is_file():
        raise RunnerError(f"Missing area-reader v2 runner module: {area_reader_runner.__file__}", 2)
    if args.mode == "plan-only" and args.dry_run_implementation:
        raise RunnerError("--dry-run-implementation is only valid for implement or pr mode", 2)


def resolve_provider_configs(args: argparse.Namespace) -> tuple[ModelConfig, ModelConfig]:
    file_config = load_provider_config(args.provider_config)
    defaults = {
        "reader": default_ollama_command_config(DEFAULT_READER_MODEL),
        "coder": default_ollama_command_config(DEFAULT_CODER_MODEL),
    }
    reader = resolve_model_config(
        "reader",
        defaults=defaults["reader"],
        file_config=file_config,
        cli_values=provider_cli_values(args, "reader", file_config, defaults["reader"]),
    )
    coder = resolve_model_config(
        "coder",
        defaults=defaults["coder"],
        file_config=file_config,
        cli_values=provider_cli_values(args, "coder", file_config, defaults["coder"]),
    )
    return reader, coder


def default_ollama_command_config(model: str) -> dict[str, object]:
    return {
        "provider": "command",
        "model": model,
        "command": ollama_command_for_model(model),
        "timeout_seconds": 600,
    }


def provider_cli_values(
    args: argparse.Namespace,
    role: str,
    file_config: dict[str, object] | None = None,
    defaults: dict[str, object] | None = None,
) -> dict[str, object]:
    values: dict[str, object] = {
        "provider": getattr(args, f"{role}_provider"),
        "command": getattr(args, f"{role}_command"),
        "base_url": getattr(args, f"{role}_base_url"),
        "model": getattr(args, f"{role}_model"),
        "api_key_env": getattr(args, f"{role}_api_key_env"),
        "timeout_seconds": getattr(args, f"{role}_timeout_seconds"),
    }
    if file_config is not None and defaults is not None:
        add_default_ollama_command(role, values, file_config, defaults)
    return values


def add_default_ollama_command(
    role: str,
    cli_values: dict[str, object],
    file_config: dict[str, object],
    defaults: dict[str, object],
) -> None:
    role_config = file_config.get(role, {})
    if role_config and not isinstance(role_config, dict):
        return
    role_values = role_config if isinstance(role_config, dict) else {}
    provider = cli_values.get("provider") or role_values.get("provider") or defaults.get("provider")
    command = cli_values.get("command") or role_values.get("command")
    if provider != "command" or command:
        return
    model = cli_values.get("model") or role_values.get("model") or defaults.get("model")
    if model:
        cli_values["command"] = ollama_command_for_model(str(model))


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
    input_text: str | None = None,
) -> CommandResult:
    print_command(argv, cwd, stream)
    completed = subprocess.run(
        argv,
        cwd=cwd,
        input=input_text,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    result = CommandResult(argv, cwd, completed.returncode, completed.stdout, completed.stderr)
    if check and result.returncode != 0:
        raise RunnerError(format_command_failure(result))
    return result


def format_command_failure(result: CommandResult) -> str:
    return "\n".join(
        part
        for part in (
            f"Command failed with exit code {result.returncode}: {subprocess.list2cmdline(result.argv)}",
            result.stdout.strip(),
            result.stderr.strip(),
        )
        if part
    )


def select_issue(args: argparse.Namespace, repo: Path, stream: TextIO) -> IssueSelection:
    if args.issue:
        return IssueSelection(number=args.issue, title="", url="", labels=[])
    result = run_command(
        [
            "gh",
            "issue",
            "list",
            "--repo",
            args.github_repo,
            "--state",
            "open",
            "--label",
            args.ready_label,
            "--limit",
            str(args.limit),
            "--json",
            "number,title,url,labels,createdAt",
        ],
        cwd=repo,
        stream=stream,
    )
    try:
        issues = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RunnerError(f"gh issue list returned invalid JSON: {exc}") from exc
    selected = select_next_issue(
        issues,
        running_label=args.running_label,
        blocked_label=args.blocked_label,
        selection=args.selection,
    )
    if selected is None:
        raise RunnerError("No eligible AutoDev issue found.", 2)
    print(f"Selected issue #{selected.number}: {selected.title}", file=stream)
    return selected


def select_next_issue(
    issues: list[dict[str, object]],
    *,
    running_label: str,
    blocked_label: str,
    selection: str,
) -> IssueSelection | None:
    eligible = []
    for issue in issues:
        labels = [
            str(label.get("name"))
            for label in issue.get("labels", [])
            if isinstance(label, dict) and label.get("name")
        ]
        if running_label in labels or blocked_label in labels:
            continue
        eligible.append(issue)
    if not eligible:
        return None
    reverse = selection == "newest"
    eligible.sort(key=lambda item: str(item.get("createdAt") or ""), reverse=reverse)
    chosen = eligible[0]
    return IssueSelection(
        number=int(chosen["number"]),
        title=str(chosen.get("title") or ""),
        url=str(chosen.get("url") or ""),
        labels=[
            str(label.get("name"))
            for label in chosen.get("labels", [])
            if isinstance(label, dict) and label.get("name")
        ],
    )


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
    return issue_text_from_json(issue, github_repo, issue_data)


def issue_text_from_json(issue: int, github_repo: str, issue_data: dict[str, object]) -> str:
    labels = issue_data.get("labels") or []
    label_names = [
        str(label.get("name"))
        for label in labels
        if isinstance(label, dict) and label.get("name")
    ]
    return "\n".join(
        [
            f"# GitHub Issue #{issue}: {str(issue_data.get('title') or '').strip()}",
            "",
            f"URL: {str(issue_data.get('url') or '').strip()}",
            "",
            f"Repository: {github_repo}",
            "",
            "Labels: " + (", ".join(label_names) if label_names else "(none)"),
            "",
            str(issue_data.get("body") or "").strip(),
            "",
        ]
    )


def update_issue_labels(
    repo: Path,
    github_repo: str,
    issue: int,
    *,
    add: list[str],
    remove: list[str],
    stream: TextIO,
) -> None:
    for label in add:
        run_command(["gh", "issue", "edit", str(issue), "--repo", github_repo, "--add-label", label], cwd=repo, stream=stream)
    for label in remove:
        run_command(["gh", "issue", "edit", str(issue), "--repo", github_repo, "--remove-label", label], cwd=repo, stream=stream)


def ensure_clean_worktree(repo: Path, stream: TextIO) -> None:
    result = run_command(["git", "status", "--porcelain"], cwd=repo, stream=stream)
    if result.stdout.strip():
        raise RunnerError("Refusing to run with uncommitted changes. Commit, stash, or pass --allow-dirty.", 2)


def issue_branch_name(issue: int, issue_text: str) -> str:
    title_line = next((line for line in issue_text.splitlines() if line.startswith(f"# GitHub Issue #{issue}:")), "")
    title = title_line.split(":", 1)[1] if ":" in title_line else f"issue-{issue}"
    slug = re.sub(r"[^a-z0-9]+", "-", title.casefold()).strip("-") or "real-issue"
    return f"autodev/issue-{issue}-{slug[:60]}"


def ensure_issue_branch(repo: Path, branch_name: str, stream: TextIO) -> None:
    current = run_command(["git", "branch", "--show-current"], cwd=repo, stream=stream).stdout.strip()
    if current == branch_name:
        return
    if current in {"main", "master"} or current.startswith("autodev/") or current.startswith("codex/"):
        run_command(["git", "switch", "-c", branch_name], cwd=repo, stream=stream)
        return
    raise RunnerError(
        f"Refusing to branch from unexpected current branch '{current}'. "
        "Start from main or an existing AutoDev branch.",
        2,
    )


def run_area_reader(
    repo: Path,
    issue_text: str,
    reader_config: ModelConfig,
    coder_config: ModelConfig,
    out_dir: Path,
    stream: TextIO,
) -> None:
    if out_dir.exists():
        shutil.rmtree(out_dir)
    argv = [
        "--repo",
        str(repo),
        "--reader-provider",
        reader_config.provider,
        "--reader-model",
        reader_config.model,
        "--reader-timeout-seconds",
        str(reader_config.timeout_seconds),
        "--coder-provider",
        coder_config.provider,
        "--coder-model",
        coder_config.model,
        "--coder-timeout-seconds",
        str(coder_config.timeout_seconds),
        "--issue",
        issue_text,
        "--out",
        str(out_dir),
    ]
    append_provider_command_args(argv, "reader", reader_config)
    append_provider_command_args(argv, "coder", coder_config)
    print("Running shared area-reader v2 planner", file=stream)
    exit_code = area_reader_runner.main(argv)
    if exit_code:
        raise RunnerError(f"area-reader v2 planner failed with exit code {exit_code}", exit_code)


def append_provider_command_args(command: list[str], role: str, config: ModelConfig) -> None:
    if config.command:
        command.extend([f"--{role}-command", config.command])
    if config.base_url:
        command.extend([f"--{role}-base-url", config.base_url])
    if config.api_key_env:
        command.extend([f"--{role}-api-key-env", config.api_key_env])


def write_operational_outputs(issue_text: str, area_out: Path, out_dir: Path, keep_debug: bool) -> None:
    copies = {
        "routing.json": "routed-areas.json",
        "synthesis-brief.md": "synthesized-handoff.md",
        "coder-plan.md": "coder-plan.md",
        "recommended-command-groups.json": "recommended-command-groups.json",
        "verification-command-groups.json": "verification-command-groups.json",
        "detected-facts.json": "detected-facts.json",
        "summary.json": "area-reader-summary.json",
    }
    write_text(out_dir / "issue.md", issue_text)
    for source_name, target_name in copies.items():
        source = area_out / source_name
        if source.is_file():
            shutil.copyfile(source, out_dir / target_name)
    write_text(out_dir / "run-summary.md", build_run_summary(out_dir))
    if not keep_debug:
        shutil.rmtree(area_out, ignore_errors=True)


def build_run_summary(out_dir: Path) -> str:
    routing = read_json(out_dir / "routed-areas.json")
    recommendations = read_json(out_dir / "recommended-command-groups.json")
    areas = routing.get("areas", []) if isinstance(routing, dict) else []
    groups = recommendations.get("recommended_command_groups", []) if isinstance(recommendations, dict) else []
    return "\n".join(
        [
            "# AutoDev Real-Issue Run Summary",
            "",
            "Routed areas: " + (", ".join(str(area) for area in areas) if areas else "(none recorded)"),
            "Recommended verification groups: "
            + (", ".join(str(group) for group in groups) if groups else "(none recorded)"),
            "",
            "Primary outputs: issue.md, selected-issue.json, routed-areas.json, synthesized-handoff.md, "
            "coder-plan.md, recommended-command-groups.json, implementation-prompt.md, model-responses/, "
            "model-patches/, verification/, verification-result-summary.md, final-pr-summary.md, provider-metadata.json",
            "",
        ]
    )


def run_implementation_loop(
    *,
    repo: Path,
    out_dir: Path,
    issue_text: str,
    branch_name: str,
    coder_provider: ModelProvider,
    coder_config: ModelConfig,
    max_fix_attempts: int,
    dry_run: bool,
    stream: TextIO,
) -> VerificationResult:
    prompt = build_implementation_prompt(
        issue_text=issue_text,
        synthesized_handoff=read_optional_text(out_dir / "synthesized-handoff.md"),
        coder_plan=read_optional_text(out_dir / "coder-plan.md"),
        recommended_command_groups=read_optional_text(out_dir / "recommended-command-groups.json"),
        constraints=read_optional_text(PROMPT_TEMPLATE_DIR / "implementer.md"),
        branch_name=branch_name,
    )
    write_text(out_dir / "implementation-prompt.md", prompt)
    response = call_coder(coder_provider, coder_config, prompt, out_dir, 0)
    patch = process_model_response(response, out_dir, 0)
    if patch is None:
        verification = VerificationResult(0, 0, "no-change", "NO_CHANGES_REQUIRED", "", out_dir / "verification" / "attempt-0.md")
        write_verification_result(out_dir, verification)
        return verification
    if dry_run:
        return VerificationResult(0, 0, "dry-run", "Dry-run implementation did not apply patch.", "", out_dir / "verification" / "attempt-0.md")
    apply_patch_file(repo, patch, stream)

    verification = run_recommended_verification(out_dir, repo, 0, stream)
    write_verification_result(out_dir, verification)
    attempt = 1
    while not verification.passed and attempt <= max_fix_attempts:
        fix_prompt = build_fix_prompt(
            issue_text=issue_text,
            synthesized_handoff=read_optional_text(out_dir / "synthesized-handoff.md"),
            coder_plan=read_optional_text(out_dir / "coder-plan.md"),
            previous_response=read_optional_text(out_dir / "model-responses" / f"attempt-{attempt - 1}.txt"),
            current_diff=current_diff(repo, stream),
            verification=verification,
        )
        write_text(out_dir / "fix-prompt.md", fix_prompt)
        response = call_coder(coder_provider, coder_config, fix_prompt, out_dir, attempt)
        patch = process_model_response(response, out_dir, attempt)
        if patch is None:
            break
        apply_patch_file(repo, patch, stream)
        verification = run_recommended_verification(out_dir, repo, attempt, stream)
        write_verification_result(out_dir, verification)
        attempt += 1
    return verification


def call_coder(provider: ModelProvider, config: ModelConfig, prompt: str, out_dir: Path, attempt: int) -> str:
    response = provider.generate(prompt, model=config.model, timeout_seconds=config.timeout_seconds)
    response_path = out_dir / "model-responses" / f"attempt-{attempt}.txt"
    write_text(response_path, response)
    return response


def process_model_response(response: str, out_dir: Path, attempt: int) -> Path | None:
    no_change = parse_no_changes_required(response)
    if no_change is not None:
        write_text(out_dir / "model-patches" / f"attempt-{attempt}.txt", NO_CHANGES_REQUIRED + "\n" + no_change.strip() + "\n")
        return None
    patch_text = extract_unified_diff(response)
    if not patch_text:
        raise RunnerError("model response did not contain a valid patch or NO_CHANGES_REQUIRED")
    patch_path = out_dir / "model-patches" / f"attempt-{attempt}.patch"
    write_text(patch_path, patch_text)
    return patch_path


def extract_unified_diff(response: str) -> str:
    start = response.find(PATCH_START)
    end = response.find(PATCH_END)
    if start < 0 or end < 0 or end <= start:
        return ""
    patch = response[start + len(PATCH_START):end].strip()
    if not patch.startswith("diff --git ") and not patch.startswith("--- "):
        return ""
    return patch + "\n"


def parse_no_changes_required(response: str) -> str | None:
    stripped = response.strip()
    if stripped == NO_CHANGES_REQUIRED:
        return ""
    if stripped.startswith(NO_CHANGES_REQUIRED + "\n"):
        return stripped[len(NO_CHANGES_REQUIRED):].strip()
    return None


def apply_patch_file(repo: Path, patch_path: Path, stream: TextIO) -> None:
    result = run_command(["git", "apply", "--index", str(patch_path)], cwd=repo, stream=stream, check=False)
    if result.returncode == 0:
        return
    fallback = run_command(["git", "apply", str(patch_path)], cwd=repo, stream=stream, check=False)
    if fallback.returncode != 0:
        raise RunnerError("patch application failed\n" + format_command_failure(fallback))


def run_recommended_verification(out_dir: Path, repo: Path, attempt: int, stream: TextIO) -> VerificationResult:
    groups = read_json(out_dir / "verification-command-groups.json")
    recommendations = read_json(out_dir / "recommended-command-groups.json")
    recommended = recommendations.get("recommended_command_groups", []) if isinstance(recommendations, dict) else []
    selected = [
        group for group in groups
        if isinstance(group, dict) and group.get("name") in recommended and not group.get("manual")
    ] if isinstance(groups, list) else []
    stdout_parts: list[str] = []
    stderr_parts: list[str] = []
    for group in selected:
        group_name = str(group.get("name") or "unknown")
        stdout_parts.append(f"== {group_name} ==")
        commands = group.get("commands") or []
        if not commands:
            stdout_parts.append(str(group.get("reason") or "No commands in this group."))
            continue
        for item in commands:
            if not isinstance(item, dict):
                continue
            argv = [str(part) for part in item.get("argv", [])]
            if not argv:
                continue
            cwd = repo / str(item.get("cwd") or ".")
            result = run_command(argv, cwd=cwd, stream=stream, check=False)
            stdout_parts.append(result.stdout)
            if result.stderr:
                stderr_parts.append(result.stderr)
            if result.returncode != 0 and not item.get("optional"):
                verification = VerificationResult(
                    attempt,
                    result.returncode,
                    group_name,
                    "\n".join(stdout_parts),
                    "\n".join(stderr_parts),
                    out_dir / "verification" / f"attempt-{attempt}.md",
                )
                write_verification_attempt(verification)
                return verification
    verification = VerificationResult(
        attempt,
        0,
        ",".join(str(group.get("name")) for group in selected) or "none",
        "\n".join(stdout_parts),
        "\n".join(stderr_parts),
        out_dir / "verification" / f"attempt-{attempt}.md",
    )
    write_verification_attempt(verification)
    return verification


def write_verification_attempt(result: VerificationResult) -> None:
    write_text(result.summary_path, render_verification_summary(result))


def write_verification_result(out_dir: Path, result: VerificationResult) -> None:
    write_text(out_dir / "verification-result-summary.md", render_verification_summary(result))


def render_verification_summary(result: VerificationResult) -> str:
    return "\n".join(
        [
            "# Verification Result Summary",
            "",
            f"Attempt: {result.attempt}",
            f"Command group: {result.command_group}",
            f"Exit code: {result.returncode}",
            "",
            "## Output",
            "",
            trim_log(result.stdout),
            "",
            "## Error Output",
            "",
            trim_log(result.stderr),
            "",
        ]
    )


def collect_area_reader_relevant_files(out_dir: Path, workspace_snapshot: object) -> list[str]:
    workspace_paths = set(workspace_snapshot.keys()) if isinstance(workspace_snapshot, dict) else set()
    files: set[str] = set()
    summary = read_json(out_dir / "area-reader-summary.json")
    if not summary:
        summary = read_json(out_dir / "summary.json")
    if isinstance(summary, dict):
        area_metadata = summary.get("area_metadata")
        if isinstance(area_metadata, dict):
            for metadata in area_metadata.values():
                if not isinstance(metadata, dict):
                    continue
                for path in metadata.get("included_files", []):
                    add_workspace_path(files, str(path), workspace_paths)
        collect_workspace_paths(summary.get("detected_facts"), files, workspace_paths)
    collect_workspace_paths(read_json(out_dir / "detected-facts.json"), files, workspace_paths)
    return sorted(files)


def collect_workspace_paths(value: object, files: set[str], workspace_paths: set[str]) -> None:
    if isinstance(value, dict):
        for item in value.values():
            collect_workspace_paths(item, files, workspace_paths)
    elif isinstance(value, list):
        for item in value:
            collect_workspace_paths(item, files, workspace_paths)
    elif isinstance(value, str):
        add_workspace_path(files, value, workspace_paths)


def add_workspace_path(files: set[str], path: str, workspace_paths: set[str]) -> None:
    normalized = path.replace("\\", "/").strip()
    if not normalized or normalized.startswith("/"):
        return
    if workspace_paths and normalized not in workspace_paths:
        return
    if any(marker in normalized for marker in ("\n", "\r", "*")):
        return
    if "/" in normalized or "." in Path(normalized).name:
        files.add(normalized)


def workspace_snapshot_summary(workspace_snapshot: object, limit: int = 200) -> str:
    if not isinstance(workspace_snapshot, dict):
        return "{}"
    paths = sorted(str(path) for path in workspace_snapshot.keys())
    return json.dumps(
        {
            "path_count": len(paths),
            "paths": paths[:limit],
            "truncated": len(paths) > limit,
        },
        indent=2,
        sort_keys=True,
    )


def build_area_reader_planner_prompt(
    *,
    issue_text: str,
    local_check: str,
    labels: list[str],
    profile_context_hints: str,
    routed_areas: object,
    synthesized_handoff: str,
    coder_plan: str,
    relevant_files: list[str],
    recommended_command_groups: object,
    workspace_snapshot: object,
) -> str:
    return f"""Use the issue-to-pr-automation skill.

You are the Planner for this repository.

Operating mode: PLAN ONLY - NO CODE.

Area-reader routed areas:
{json.dumps(routed_areas, indent=2, sort_keys=True)}

Area-reader synthesized handoff:
{synthesized_handoff.strip()}

Area-reader coder / implementation plan:
{coder_plan.strip()}

Detected relevant files from area-reader facts:
{json.dumps(relevant_files, indent=2, sort_keys=True)}

Recommended command groups:
{json.dumps(recommended_command_groups, indent=2, sort_keys=True)}

Workspace snapshot grounding:
{workspace_snapshot_summary(workspace_snapshot)}

Routing hints only:
- GitHub labels: {', '.join(labels) if labels else '(none)'}
- Profile context hints: {profile_context_hints.strip() or '(none)'}

Automation context:
- The configured local verification command is: {local_check}
- Build/run/tests are handled by the automation script unless explicitly stated otherwise.
- Do not modify files.

Goal:
Plan the implementation of the issue below as a fast, localized change with minimal risk.

Constraints:
- Treat labels and profile text as routing hints only. Use area-reader synthesis and repository facts as the final planning scope.
- Ground every file or path in the workspace snapshot and area-reader facts. Do not invent paths.
- Let area-reader synthesis narrow touched areas/files based on the issue text and repository facts.
- Do NOT over-decompose.
- Use at most 4 implementation steps.
- Touch as few files as possible, preferably 1-3 files.
- Prefer editing existing code over creating new abstractions.
- Avoid task stubs, TODO-only work, and speculative architecture.
- Do not change domain logic, persistence, models, migrations, public APIs, schemas, scoring, task state logic, or unrelated behavior unless the issue explicitly requires it.
- If something is unclear, make a reasonable assumption and call it out briefly.
- If the issue is too broad for a localized change, say so clearly and propose the smallest safe slice.

Output format:
1) Where to look
   - Exact search terms or likely files/components to inspect
   - Max 6 bullets
2) Files / areas likely to touch
   - Best guess list, using only existing paths supported by the workspace snapshot or area-reader facts
3) Assumptions
   - Max 5 bullets
4) Plan
   - Max 4 bullets
   - Fastest sensible implementation path
5) Risks / gotchas
   - Max 5 bullets
6) Recommended implementation approach
   - Option A: fastest / lowest-risk
   - Option B: slightly cleaner, only if Option A is blocked or too messy

Rules:
- No code.
- No pseudo-code.
- No refactoring wishlist.
- Keep the plan implementer-ready.
- Output only the final plan. Do not include thinking, hidden reasoning, scratchpad text, or model preamble.

Issue:
{issue_text}
"""


def build_planner_prompt_from_area_reader(current_dir: Path, issue_text: str, local_check: str, labels: list[str], profile_context_hints: str) -> str:
    workspace_snapshot = read_json(current_dir / "workspace-snapshot.json")
    return build_area_reader_planner_prompt(
        issue_text=issue_text,
        local_check=local_check,
        labels=labels,
        profile_context_hints=profile_context_hints,
        routed_areas=read_json(current_dir / "routed-areas.json"),
        synthesized_handoff=read_optional_text(current_dir / "synthesized-handoff.md"),
        coder_plan=read_optional_text(current_dir / "coder-plan.md"),
        relevant_files=collect_area_reader_relevant_files(current_dir, workspace_snapshot),
        recommended_command_groups=read_json(current_dir / "recommended-command-groups.json"),
        workspace_snapshot=workspace_snapshot,
    )

def build_implementation_prompt(
    *,
    issue_text: str,
    synthesized_handoff: str,
    coder_plan: str,
    recommended_command_groups: str,
    constraints: str,
    branch_name: str,
) -> str:
    return f"""You are the coder model for AutoDev.

The AutoDev runner will apply your patch and run deterministic verification. You must not run shell commands.

Issue:
{issue_text}

Synthesized handoff:
{synthesized_handoff}

Coder plan:
{coder_plan}

Recommended command groups JSON:
{recommended_command_groups}

Repository constraints:
{constraints}

Current branch:
{branch_name}

Rules:
- Make minimal, issue-scoped changes.
- Avoid unrelated refactors.
- Preserve existing style and file organization.
- Output only one of the required response shapes.
- Use a unified git diff inside {PATCH_START} and {PATCH_END}.
- Output {NO_CHANGES_REQUIRED} only if the issue is already fully satisfied.
- Do not include prose outside the required markers.

Patch response contract:
{PATCH_START}
<unified git diff>
{PATCH_END}

No-change response contract:
{NO_CHANGES_REQUIRED}
<short explanation>
"""


def build_fix_prompt(
    *,
    issue_text: str,
    synthesized_handoff: str,
    coder_plan: str,
    previous_response: str,
    current_diff: str,
    verification: VerificationResult,
) -> str:
    return f"""You are the fixer model for AutoDev.

Produce a minimal corrective unified diff only. The AutoDev runner will apply it and rerun verification.

Original issue:
{issue_text}

Synthesized handoff:
{synthesized_handoff}

Coder plan:
{coder_plan}

Previous model response summary:
{trim_log(previous_response, 4000)}

Current git diff:
{current_diff}

Verification exit code:
{verification.returncode}

Failed command group:
{verification.command_group}

Verification stdout:
{trim_log(verification.stdout)}

Verification stderr:
{trim_log(verification.stderr)}

Output only:
{PATCH_START}
<minimal corrective unified git diff>
{PATCH_END}
"""


def write_implementation_prompt_file(out_dir: Path, issue_text: str, branch_name: str) -> None:
    write_text(
        out_dir / "implementation-prompt.md",
        build_implementation_prompt(
            issue_text=issue_text,
            synthesized_handoff=read_optional_text(out_dir / "synthesized-handoff.md"),
            coder_plan=read_optional_text(out_dir / "coder-plan.md"),
            recommended_command_groups=read_optional_text(out_dir / "recommended-command-groups.json"),
            constraints=read_optional_text(PROMPT_TEMPLATE_DIR / "implementer.md"),
            branch_name=branch_name,
        ),
    )


def current_diff(repo: Path, stream: TextIO) -> str:
    result = run_command(["git", "diff"], cwd=repo, stream=stream, check=False)
    return result.stdout


def create_draft_pr(
    repo: Path,
    github_repo: str,
    issue: int,
    issue_text: str,
    out_dir: Path,
    reader_config: ModelConfig,
    coder_config: ModelConfig,
    stream: TextIO,
) -> str:
    current_branch = run_command(["git", "branch", "--show-current"], cwd=repo, stream=stream).stdout.strip()
    if current_branch in {"main", "master"}:
        raise RunnerError("Refusing to create a PR from the main branch.", 2)
    changed_paths = changed_worktree_paths(repo, stream)
    run_artifacts = [path for path in changed_paths if is_relative_to(repo / path, out_dir)]
    if run_artifacts:
        raise RunnerError("Refusing pr mode because --out files would be committed: " + ", ".join(run_artifacts), 2)
    if not changed_paths:
        raise RunnerError("No changes detected for PR mode after verification.", 2)
    run_command(["git", "add", "--", *changed_paths], cwd=repo, stream=stream)
    run_command(["git", "commit", "-m", f"Implement issue {issue} with AutoDev runner"], cwd=repo, stream=stream)
    run_command(["git", "push", "-u", "origin", current_branch], cwd=repo, stream=stream)
    body_path = out_dir / "draft-pr-body.md"
    body = build_pr_body(issue, issue_text, out_dir, reader_config, coder_config)
    write_text(body_path, body)
    title = first_issue_title(issue_text) or f"Issue #{issue}"
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


def changed_worktree_paths(repo: Path, stream: TextIO) -> list[str]:
    result = run_command(["git", "status", "--porcelain"], cwd=repo, stream=stream)
    paths = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        path = line[3:].strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        paths.append(path)
    return paths


def is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def build_pr_body(
    issue: int,
    issue_text: str,
    out_dir: Path,
    reader_config: ModelConfig,
    coder_config: ModelConfig,
) -> str:
    return "\n".join(
        [
            f"Closes #{issue}",
            "",
            "Generated by AutoDev.",
            "",
            "## Summary",
            "",
            read_optional_text(out_dir / "coder-plan.md").strip() or "See implementation diff.",
            "",
            "## Verification",
            "",
            read_optional_text(out_dir / "verification-result-summary.md").strip(),
            "",
            "## Provider Metadata",
            "",
            "```json",
            json.dumps(
                {"reader": reader_config.safe_metadata(), "coder": coder_config.safe_metadata()},
                indent=2,
                sort_keys=True,
            ),
            "```",
            "",
        ]
    )


def first_issue_title(issue_text: str) -> str:
    for line in issue_text.splitlines():
        if line.startswith("# GitHub Issue") and ":" in line:
            return line.split(":", 1)[1].strip()
    return ""


def write_provider_metadata(out_dir: Path, reader_config: ModelConfig, coder_config: ModelConfig) -> None:
    write_json(
        out_dir / "provider-metadata.json",
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "reader": reader_config.safe_metadata(),
            "coder": coder_config.safe_metadata(),
        },
    )


def read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def read_optional_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def write_json(path: Path, value: object) -> None:
    write_text(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def trim_log(value: str, limit: int = 12000) -> str:
    if len(value) <= limit:
        return value.rstrip() + "\n"
    return value[-limit:].rstrip() + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
