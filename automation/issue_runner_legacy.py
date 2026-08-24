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
from area_reader import workflow as area_reader_runner
from area_reader.recommendations import documentation_only_command_groups, is_documentation_only_scope
from automation.model_output_sanitizer import sanitize_model_output
from automation.model_providers import (
    ModelConfig,
    ModelProvider,
    ProviderError,
    create_provider,
    load_provider_config,
    ollama_command_for_model,
    resolve_model_config,
)
from automation.issue_runner_artifacts import (
    write_operational_outputs,
    write_provider_metadata,
)
from automation.issue_runner_commands import (
    require_tools,
)
from automation.issue_runner_config import (
    expand_path,
    parse_args,
    resolve_provider_configs,
    validate_inputs,
)
from automation.issue_runner_contract import (
    RunnerError,
)
from automation.issue_runner_implementation import (
    run_implementation_loop,
)
from automation.issue_runner_prompts import (
    write_implementation_prompt_file,
)
from automation.issue_runner_pull_request import (
    create_draft_pr,
)
from automation.issue_runner_reader import (
    run_area_reader,
)
from automation.issue_runner_repository import (
    ensure_clean_worktree,
    ensure_issue_branch,
    fetch_issue_text,
    issue_branch_name,
    select_issue,
    update_issue_labels,
)
from automation.issue_runner_storage import (
    write_json,
    write_text,
)
from automation.issue_runner_verification import (
    render_verification_summary,
    run_recommended_verification,
)

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
