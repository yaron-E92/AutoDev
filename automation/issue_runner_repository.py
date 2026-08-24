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
from automation.issue_runner_commands import (
    run_command,
)
from automation.issue_runner_contract import (
    IssueSelection,
    RunnerError,
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
