from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Callable, TextIO

from automation import repository_identity
from automation.queue_contract import (
    API_VERSION,
    Blocker,
    CommandResult,
    DEFAULT_LIMIT,
    LABEL_SPECS,
    QueueError,
    QueueIssue,
    _label_names,
    _milestone_title,
)

def _run_gh(
    repo: Path,
    arguments: list[str],
    *,
    runner: Callable[..., object] = subprocess.run,
    check: bool = True,
) -> CommandResult:
    try:
        completed = runner(
            ["gh", *arguments],
            cwd=repo,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        raise QueueError(f"cannot execute gh: {exc}") from exc
    result = CommandResult(
        tuple(["gh", *arguments]),
        int(getattr(completed, "returncode", 1)),
        str(getattr(completed, "stdout", "") or ""),
        str(getattr(completed, "stderr", "") or ""),
    )
    if check and result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "no command output"
        raise QueueError(
            f"GitHub command failed ({result.returncode}): {' '.join(result.argv)}: {detail}"
        )
    return result

def _json_result(result: CommandResult, *, context: str) -> object:
    try:
        return json.loads(result.stdout or "null")
    except json.JSONDecodeError as exc:
        raise QueueError(f"{context} returned invalid JSON") from exc

def resolve_github_repo(
    repo: Path,
    *,
    explicit: str = "",
    runner: Callable[..., object] = subprocess.run,
) -> str:
    value = explicit.strip()
    if value and value.count("/") != 1:
        raise QueueError("--github-repo must use owner/name format")
    try:
        return repository_identity.resolve_github_repository(
            repo,
            explicit=value,
            runner=runner,
            allow_gh_fallback=True,
        )
    except repository_identity.RepositoryIdentityError as exc:
        raise QueueError(str(exc)) from exc

def _queue_issue(raw: dict[str, object], fallback_number: int = 0) -> QueueIssue:
    return QueueIssue(
        number=int(raw.get("number") or fallback_number),
        title=str(raw.get("title", "")),
        url=str(raw.get("url", "")),
        state=str(raw.get("state", "")).casefold(),
        labels=_label_names(raw.get("labels")),
        created_at=str(raw.get("createdAt", "")),
        milestone=_milestone_title(raw.get("milestone")),
    )

def list_issues(
    repo: Path,
    github_repo: str,
    *,
    limit: int = DEFAULT_LIMIT,
    runner: Callable[..., object] = subprocess.run,
) -> list[QueueIssue]:
    result = _run_gh(
        repo,
        [
            "issue",
            "list",
            "--repo",
            github_repo,
            "--state",
            "all",
            "--limit",
            str(limit),
            "--json",
            "number,title,url,state,labels,createdAt,milestone",
        ],
        runner=runner,
    )
    raw = _json_result(result, context="gh issue list")
    if not isinstance(raw, list):
        raise QueueError("gh issue list did not return an array")
    issues: list[QueueIssue] = []
    for item in raw:
        if not isinstance(item, dict) or not item.get("number"):
            continue
        issues.append(_queue_issue(item))
    return sorted(issues, key=lambda item: item.number)

def fetch_issue(
    repo: Path,
    github_repo: str,
    issue_number: int,
    *,
    runner: Callable[..., object] = subprocess.run,
) -> QueueIssue:
    result = _run_gh(
        repo,
        [
            "issue",
            "view",
            str(issue_number),
            "--repo",
            github_repo,
            "--json",
            "number,title,url,state,labels,createdAt,milestone",
        ],
        runner=runner,
    )
    raw = _json_result(result, context="gh issue view")
    if not isinstance(raw, dict):
        raise QueueError("gh issue view did not return an object")
    return _queue_issue(raw, fallback_number=issue_number)

def list_blockers(
    repo: Path,
    github_repo: str,
    issue_number: int,
    *,
    runner: Callable[..., object] = subprocess.run,
) -> list[Blocker]:
    endpoint = (
        f"repos/{github_repo}/issues/{issue_number}/dependencies/blocked_by?per_page=100"
    )
    result = _run_gh(
        repo,
        ["api", "-H", f"X-GitHub-Api-Version: {API_VERSION}", endpoint],
        runner=runner,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "no command output"
        raise QueueError(
            "GitHub native issue dependencies are unavailable; AutoDev will not infer blockers "
            f"from issue prose: {detail}"
        )
    raw = _json_result(result, context="GitHub blocked-by API")
    if not isinstance(raw, list):
        raise QueueError("GitHub blocked-by API did not return an array")
    blockers: list[Blocker] = []
    for item in raw:
        if not isinstance(item, dict) or not item.get("id") or not item.get("number"):
            continue
        blockers.append(
            Blocker(
                id=int(item["id"]),
                number=int(item["number"]),
                title=str(item.get("title", "")),
                url=str(item.get("html_url") or item.get("url") or ""),
                state=str(item.get("state", "")).casefold(),
            )
        )
    return sorted(blockers, key=lambda item: item.number)

def remove_dependency(
    repo: Path,
    github_repo: str,
    issue_number: int,
    blocker_id: int,
    *,
    runner: Callable[..., object] = subprocess.run,
) -> None:
    endpoint = (
        f"repos/{github_repo}/issues/{issue_number}/dependencies/blocked_by/{blocker_id}"
    )
    _run_gh(
        repo,
        [
            "api",
            "--method",
            "DELETE",
            "-H",
            f"X-GitHub-Api-Version: {API_VERSION}",
            endpoint,
        ],
        runner=runner,
    )

def ensure_queue_labels(
    repo: Path,
    github_repo: str,
    *,
    runner: Callable[..., object] = subprocess.run,
) -> tuple[str, ...]:
    result = _run_gh(
        repo,
        [
            "label",
            "list",
            "--repo",
            github_repo,
            "--limit",
            "1000",
            "--json",
            "name",
        ],
        runner=runner,
    )
    raw = _json_result(result, context="gh label list")
    if not isinstance(raw, list):
        raise QueueError("gh label list did not return an array")
    existing = {
        str(item.get("name"))
        for item in raw
        if isinstance(item, dict) and item.get("name")
    }
    created: list[str] = []
    for name, (color, description) in LABEL_SPECS.items():
        if name in existing:
            continue
        _run_gh(
            repo,
            [
                "label",
                "create",
                name,
                "--repo",
                github_repo,
                "--color",
                color,
                "--description",
                description,
            ],
            runner=runner,
        )
        created.append(name)
    return tuple(created)


def add_issue_label(
    repo: Path,
    github_repo: str,
    issue_number: int,
    label: str,
    *,
    runner: Callable[..., object] = subprocess.run,
) -> None:
    """Add one label without replacing any existing issue labels."""
    _run_gh(
        repo,
        [
            "issue",
            "edit",
            str(issue_number),
            "--repo",
            github_repo,
            "--add-label",
            label,
        ],
        runner=runner,
    )
