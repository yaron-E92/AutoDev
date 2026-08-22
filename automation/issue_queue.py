from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, TextIO


MANAGED_LABEL = "autodev:managed"
READY_LABEL = "autodev:ready"
BLOCKED_LABEL = "autodev:blocked"
ATTENTION_LABEL = "autodev:attention"
RUNNING_LABEL = "autodev:running"
QUEUE_CONFIG = Path(".autodev") / "queue.json"
API_VERSION = "2026-03-10"
DEFAULT_LIMIT = 1000

LABEL_SPECS = {
    MANAGED_LABEL: ("1d76db", "Human authorization for autonomous AutoDev work"),
    READY_LABEL: ("0e8a16", "Derived: managed and currently runnable by AutoDev"),
    BLOCKED_LABEL: ("d93f0b", "Derived: managed but blocked by open issue dependencies"),
    ATTENTION_LABEL: ("fbca04", "Human attention is required before autonomous AutoDev work"),
    RUNNING_LABEL: ("5319e7", "Active AutoDev claim/run for this issue"),
}


class QueueError(RuntimeError):
    pass


@dataclass(frozen=True)
class QueuePolicy:
    autonomous_execution: bool = True


@dataclass(frozen=True)
class QueueIssue:
    number: int
    title: str
    url: str
    state: str
    labels: tuple[str, ...]
    created_at: str = ""
    milestone: str = ""


@dataclass(frozen=True)
class Blocker:
    id: int
    number: int
    title: str
    url: str
    state: str


@dataclass(frozen=True)
class QueueState:
    issue: QueueIssue
    reason: str
    open_blockers: tuple[Blocker, ...] = ()
    closed_blockers: tuple[Blocker, ...] = ()
    changed: bool = False
    removed_closed_dependencies: tuple[int, ...] = ()


@dataclass(frozen=True)
class CommandResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


def _label_names(raw: object) -> tuple[str, ...]:
    if not isinstance(raw, list):
        return ()
    names: list[str] = []
    for item in raw:
        if isinstance(item, dict) and item.get("name"):
            names.append(str(item["name"]))
        elif isinstance(item, str) and item:
            names.append(item)
    return tuple(sorted(set(names)))


def _milestone_title(raw: object) -> str:
    if isinstance(raw, dict):
        return str(raw.get("title", ""))
    if isinstance(raw, str):
        return raw
    return ""


def load_policy(repo: Path) -> QueuePolicy:
    path = repo.expanduser().resolve() / QUEUE_CONFIG
    if not path.is_file():
        return QueuePolicy()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise QueueError(f"invalid queue policy JSON: {path}") from exc
    if not isinstance(raw, dict):
        raise QueueError(f"queue policy must be a JSON object: {path}")
    version = raw.get("version", 1)
    if version != 1:
        raise QueueError(f"unsupported queue policy version: {version}")
    value = raw.get("autonomous_execution", True)
    if not isinstance(value, bool):
        raise QueueError("queue policy autonomous_execution must be true or false")
    return QueuePolicy(autonomous_execution=value)


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
    if value:
        if value.count("/") != 1:
            raise QueueError("--github-repo must use owner/name format")
        return value
    owner = os.environ.get("GITHUB_OWNER", "").strip()
    name = os.environ.get("GITHUB_REPO", "").strip()
    if owner and name:
        return f"{owner}/{name}"
    result = _run_gh(
        repo,
        ["repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"],
        runner=runner,
    )
    value = result.stdout.strip()
    if value.count("/") != 1:
        raise QueueError("could not resolve GitHub repository identity")
    return value


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


def _split_blockers(
    blockers: list[Blocker],
) -> tuple[tuple[Blocker, ...], tuple[Blocker, ...]]:
    open_items = tuple(item for item in blockers if item.state == "open")
    closed_items = tuple(item for item in blockers if item.state != "open")
    return open_items, closed_items


def classify_issue(
    issue: QueueIssue,
    blockers: list[Blocker] | tuple[Blocker, ...],
    policy: QueuePolicy,
) -> QueueState:
    labels = set(issue.labels)
    open_blockers, closed_blockers = _split_blockers(list(blockers))
    if issue.state != "open":
        reason = "closed"
    elif MANAGED_LABEL not in labels:
        reason = "unmanaged"
    elif open_blockers:
        reason = "blocked"
    elif ATTENTION_LABEL in labels:
        reason = "attention"
    elif RUNNING_LABEL in labels:
        reason = "running"
    elif not policy.autonomous_execution:
        reason = "policy-excluded"
    else:
        reason = "ready"
    return QueueState(
        issue=issue,
        reason=reason,
        open_blockers=open_blockers,
        closed_blockers=closed_blockers,
    )


def _desired_derived_labels(state: QueueState) -> tuple[bool, bool]:
    return state.reason == "ready", state.reason == "blocked"


def _update_derived_labels(
    repo: Path,
    github_repo: str,
    state: QueueState,
    *,
    runner: Callable[..., object] = subprocess.run,
) -> bool:
    labels = set(state.issue.labels)
    want_ready, want_blocked = _desired_derived_labels(state)
    add: list[str] = []
    remove: list[str] = []
    if want_ready and READY_LABEL not in labels:
        add.append(READY_LABEL)
    if not want_ready and READY_LABEL in labels:
        remove.append(READY_LABEL)
    if want_blocked and BLOCKED_LABEL not in labels:
        add.append(BLOCKED_LABEL)
    if not want_blocked and BLOCKED_LABEL in labels:
        remove.append(BLOCKED_LABEL)
    if not add and not remove:
        return False
    args = ["issue", "edit", str(state.issue.number), "--repo", github_repo]
    for name in add:
        args.extend(["--add-label", name])
    for name in remove:
        args.extend(["--remove-label", name])
    _run_gh(repo, args, runner=runner)
    return True


def inspect_queue(
    repo: Path,
    github_repo: str,
    *,
    limit: int = DEFAULT_LIMIT,
    runner: Callable[..., object] = subprocess.run,
) -> list[QueueState]:
    repo = repo.expanduser().resolve()
    policy = load_policy(repo)
    states: list[QueueState] = []
    for issue in list_issues(repo, github_repo, limit=limit, runner=runner):
        labels = set(issue.labels)
        if not labels.intersection(
            {
                MANAGED_LABEL,
                READY_LABEL,
                BLOCKED_LABEL,
                ATTENTION_LABEL,
                RUNNING_LABEL,
            }
        ):
            continue
        blockers: list[Blocker] = []
        if issue.state == "open" and MANAGED_LABEL in labels:
            blockers = list_blockers(repo, github_repo, issue.number, runner=runner)
        states.append(classify_issue(issue, blockers, policy))
    return states


def reconcile_queue(
    repo: Path,
    github_repo: str,
    *,
    limit: int = DEFAULT_LIMIT,
    runner: Callable[..., object] = subprocess.run,
) -> tuple[list[QueueState], tuple[str, ...]]:
    repo = repo.expanduser().resolve()
    created_labels = ensure_queue_labels(repo, github_repo, runner=runner)
    policy = load_policy(repo)
    states: list[QueueState] = []
    for issue in list_issues(repo, github_repo, limit=limit, runner=runner):
        labels = set(issue.labels)
        if not labels.intersection(
            {
                MANAGED_LABEL,
                READY_LABEL,
                BLOCKED_LABEL,
                ATTENTION_LABEL,
                RUNNING_LABEL,
            }
        ):
            continue
        blockers: list[Blocker] = []
        removed: list[int] = []
        if issue.state == "open" and MANAGED_LABEL in labels:
            blockers = list_blockers(repo, github_repo, issue.number, runner=runner)
            for blocker in blockers:
                if blocker.state == "open":
                    continue
                remove_dependency(
                    repo,
                    github_repo,
                    issue.number,
                    blocker.id,
                    runner=runner,
                )
                removed.append(blocker.number)
            blockers = [item for item in blockers if item.state == "open"]
        state = classify_issue(issue, blockers, policy)
        changed = _update_derived_labels(repo, github_repo, state, runner=runner)
        states.append(
            QueueState(
                issue=state.issue,
                reason=state.reason,
                open_blockers=state.open_blockers,
                closed_blockers=state.closed_blockers,
                changed=changed,
                removed_closed_dependencies=tuple(sorted(removed)),
            )
        )
    return states, created_labels


def queue_summary(states: list[QueueState]) -> dict[str, int]:
    open_managed = [
        state
        for state in states
        if state.issue.state == "open" and MANAGED_LABEL in state.issue.labels
    ]
    return {
        "managed": len(open_managed),
        "ready": sum(state.reason == "ready" for state in open_managed),
        "dependency_blocked": sum(
            state.reason == "blocked" for state in open_managed
        ),
        "attention_required": sum(
            ATTENTION_LABEL in state.issue.labels for state in open_managed
        ),
        "running": sum(state.reason == "running" for state in open_managed),
        "policy_excluded": sum(
            state.reason == "policy-excluded" for state in open_managed
        ),
    }


def explain_state(state: QueueState) -> str:
    number = state.issue.number
    if state.reason == "blocked":
        blockers = ", ".join(
            f"#{item.number} {item.title}".strip() for item in state.open_blockers
        )
        return f"#{number} blocked by: {blockers}"
    explanations = {
        "ready": "managed, open, dependency-free, and eligible for autonomous execution",
        "attention": "requires human attention",
        "running": "already has an active AutoDev claim/run",
        "policy-excluded": "repository policy disables autonomous execution",
        "unmanaged": "not authorized for autonomous AutoDev work",
        "closed": "issue is closed",
    }
    return f"#{number} {state.reason}: {explanations.get(state.reason, state.reason)}"


def _state_json(state: QueueState) -> dict[str, object]:
    value = asdict(state)
    value["issue"]["labels"] = list(state.issue.labels)  # type: ignore[index]
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="autodev queue",
        description="Maintain and select AutoDev issue queue state without model calls.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("reconcile", "status"):
        command = sub.add_parser(name)
        command.add_argument("--github-repo", default="")
        command.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
        command.add_argument("--json", action="store_true")
    explain = sub.add_parser("explain")
    explain.add_argument("issue", type=int)
    explain.add_argument("--github-repo", default="")
    explain.add_argument("--json", action="store_true")
    next_command = sub.add_parser(
        "next",
        help="Select one next autonomous issue or surface the existing run.",
    )
    next_command.add_argument("--github-repo", default="")
    next_command.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    next_command.add_argument("--dry-run", action="store_true")
    next_command.add_argument("--json", action="store_true")
    return parser


def run_cli(
    argv: list[str] | None = None,
    *,
    repo: Path | None = None,
    runner: Callable[..., object] = subprocess.run,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    out = stdout or sys.stdout
    err = stderr or sys.stderr
    args = _parser().parse_args(argv)
    current = (repo or Path.cwd()).expanduser().resolve()
    try:
        github_repo = resolve_github_repo(
            current,
            explicit=str(getattr(args, "github_repo", "")),
            runner=runner,
        )
        if args.command == "reconcile":
            states, created = reconcile_queue(
                current,
                github_repo,
                limit=args.limit,
                runner=runner,
            )
            if args.json:
                print(
                    json.dumps(
                        {
                            "repository": github_repo,
                            "summary": queue_summary(states),
                            "created_labels": list(created),
                            "changed_issues": [
                                state.issue.number for state in states if state.changed
                            ],
                            "closed_dependencies_removed": {
                                str(state.issue.number): list(
                                    state.removed_closed_dependencies
                                )
                                for state in states
                                if state.removed_closed_dependencies
                            },
                        },
                        sort_keys=True,
                    ),
                    file=out,
                )
            else:
                summary = queue_summary(states)
                changed = sum(state.changed for state in states)
                removed = sum(
                    len(state.removed_closed_dependencies) for state in states
                )
                print(
                    f"AutoDev queue reconciled for {github_repo}: "
                    f"managed={summary['managed']} ready={summary['ready']} "
                    f"blocked={summary['dependency_blocked']} "
                    f"attention={summary['attention_required']} "
                    f"changed={changed} closed-dependencies-removed={removed}",
                    file=out,
                )
            return 0
        if args.command == "status":
            states = inspect_queue(
                current,
                github_repo,
                limit=args.limit,
                runner=runner,
            )
            summary = queue_summary(states)
            if args.json:
                print(
                    json.dumps(
                        {"repository": github_repo, "summary": summary},
                        sort_keys=True,
                    ),
                    file=out,
                )
            else:
                print(
                    f"AutoDev queue status for {github_repo}: "
                    f"managed={summary['managed']} ready={summary['ready']} "
                    f"blocked={summary['dependency_blocked']} "
                    f"attention={summary['attention_required']} "
                    f"running={summary['running']} "
                    f"policy-excluded={summary['policy_excluded']}",
                    file=out,
                )
            return 0
        if args.command == "next":
            from automation import queue_selection

            result = queue_selection.select_next(
                current,
                github_repo,
                limit=args.limit,
                dry_run=bool(args.dry_run),
                runner=runner,
            )
            if args.json:
                print(json.dumps(result.to_json(), sort_keys=True), file=out)
            else:
                print(queue_selection.render_selection(result), file=out)
            return 0

        issue = fetch_issue(current, github_repo, args.issue, runner=runner)
        blockers = (
            list_blockers(current, github_repo, issue.number, runner=runner)
            if issue.state == "open" and MANAGED_LABEL in issue.labels
            else []
        )
        state = classify_issue(issue, blockers, load_policy(current))
        if args.json:
            print(json.dumps(_state_json(state), sort_keys=True), file=out)
        else:
            print(explain_state(state), file=out)
        return 0
    except QueueError as exc:
        print(str(exc), file=err)
        return 2


if __name__ == "__main__":
    raise SystemExit(run_cli())
