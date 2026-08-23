from __future__ import annotations

import json
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from automation import issue_queue, opencode_resume, run_manifest, workflow_stages


ROADMAP_PATH = Path(".autodev") / "roadmap.yaml"
ROADMAP_VERSION = 1
DEFAULT_FALLBACK = "oldest"
_AUTODEV_BRANCH = re.compile(r"^autodev/issue-(\d+)(?:-|$)")


@dataclass(frozen=True)
class RoadmapRule:
    kind: str
    value: int | str
    order: int


@dataclass(frozen=True)
class Roadmap:
    version: int = ROADMAP_VERSION
    priority: tuple[RoadmapRule, ...] = ()
    fallback: str = DEFAULT_FALLBACK
    path: str = ""


@dataclass(frozen=True)
class ExistingRun:
    state: str
    issue_number: int = 0
    branch: str = ""
    next_stage: str = ""
    next_action: str = ""
    reason: str = ""


@dataclass(frozen=True)
class SelectionResult:
    state: str
    repository: str
    issue_number: int = 0
    issue_title: str = ""
    issue_url: str = ""
    source: str = ""
    explanation: str = ""
    roadmap_path: str = ""
    next_stage: str = ""
    next_action: str = ""
    branch: str = ""
    ineligible: tuple[str, ...] = ()
    dry_run: bool = False

    def to_json(self) -> dict[str, object]:
        value = asdict(self)
        value["ineligible"] = list(self.ineligible)
        return value


class RoadmapError(issue_queue.QueueError):
    pass


def _strip_yaml_comment(value: str) -> str:
    quote = ""
    escaped = False
    for index, char in enumerate(value):
        if escaped:
            escaped = False
            continue
        if char == "\\" and quote == '"':
            escaped = True
            continue
        if quote:
            if char == quote:
                quote = ""
            continue
        if char in {"'", '"'}:
            quote = char
            continue
        if char == "#" and (index == 0 or value[index - 1].isspace()):
            return value[:index].rstrip()
    return value.rstrip()


def _yaml_scalar(raw: str, *, line: int) -> str:
    value = _strip_yaml_comment(raw).strip()
    if not value:
        raise RoadmapError(f"invalid roadmap at line {line}: value must be non-empty")
    if value.startswith('"'):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise RoadmapError(
                f"invalid roadmap at line {line}: malformed quoted scalar"
            ) from exc
        if not isinstance(parsed, str) or not parsed:
            raise RoadmapError(f"invalid roadmap at line {line}: value must be a string")
        return parsed
    if value.startswith("'"):
        if len(value) < 2 or not value.endswith("'"):
            raise RoadmapError(
                f"invalid roadmap at line {line}: malformed quoted scalar"
            )
        parsed = value[1:-1].replace("''", "'")
        if not parsed:
            raise RoadmapError(f"invalid roadmap at line {line}: value must be non-empty")
        return parsed
    if value[0] in "[{&*!>|" or value.endswith(("]", "}")):
        raise RoadmapError(
            f"invalid roadmap at line {line}: v1 accepts only plain/quoted scalar priority values"
        )
    return value


def load_roadmap(repo: Path) -> Roadmap:
    repo = repo.expanduser().resolve()
    path = repo / ROADMAP_PATH
    if not path.is_file():
        return Roadmap()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RoadmapError(f"cannot read roadmap: {path}: {exc}") from exc

    version: int | None = None
    fallback: str | None = None
    rules: list[RoadmapRule] = []
    in_priority = False
    seen_top: set[str] = set()

    for line_number, original in enumerate(text.splitlines(), start=1):
        if "\t" in original:
            raise RoadmapError(
                f"invalid roadmap at line {line_number}: tabs are not supported; use spaces"
            )
        stripped_comment = _strip_yaml_comment(original)
        if not stripped_comment.strip():
            continue
        indent = len(stripped_comment) - len(stripped_comment.lstrip(" "))
        content = stripped_comment.strip()

        if indent == 0:
            in_priority = False
            if ":" not in content:
                raise RoadmapError(
                    f"invalid roadmap at line {line_number}: expected key: value"
                )
            key, raw_value = content.split(":", 1)
            key = key.strip()
            raw_value = raw_value.strip()
            if key not in {"version", "priority", "fallback"}:
                raise RoadmapError(
                    f"invalid roadmap at line {line_number}: unsupported key {key!r}"
                )
            if key in seen_top:
                raise RoadmapError(
                    f"invalid roadmap at line {line_number}: duplicate key {key!r}"
                )
            seen_top.add(key)
            if key == "priority":
                if raw_value not in {"", "[]"}:
                    raise RoadmapError(
                        f"invalid roadmap at line {line_number}: priority must be a YAML list"
                    )
                in_priority = raw_value == ""
                continue
            scalar = _yaml_scalar(raw_value, line=line_number)
            if key == "version":
                try:
                    version = int(scalar)
                except ValueError as exc:
                    raise RoadmapError(
                        f"invalid roadmap at line {line_number}: version must be an integer"
                    ) from exc
                continue
            fallback = scalar.casefold()
            continue

        if not in_priority:
            raise RoadmapError(
                f"invalid roadmap at line {line_number}: nested content is only allowed under priority"
            )
        if indent < 2 or not content.startswith("-"):
            raise RoadmapError(
                f"invalid roadmap at line {line_number}: priority entries must use '- key: value'"
            )
        entry = content[1:].strip()
        if ":" not in entry:
            raise RoadmapError(
                f"invalid roadmap at line {line_number}: priority entry must use key: value"
            )
        key, raw_value = entry.split(":", 1)
        kind = key.strip().casefold()
        if kind not in {"issue", "milestone", "label"}:
            raise RoadmapError(
                f"invalid roadmap at line {line_number}: priority kind must be issue, milestone, or label"
            )
        scalar = _yaml_scalar(raw_value, line=line_number)
        value: int | str
        if kind == "issue":
            try:
                value = int(scalar)
            except ValueError as exc:
                raise RoadmapError(
                    f"invalid roadmap at line {line_number}: issue priority must be a positive integer"
                ) from exc
            if value <= 0:
                raise RoadmapError(
                    f"invalid roadmap at line {line_number}: issue priority must be a positive integer"
                )
        else:
            value = scalar
        rules.append(RoadmapRule(kind=kind, value=value, order=len(rules)))

    if version is None:
        raise RoadmapError(f"invalid roadmap: {path} must declare version: {ROADMAP_VERSION}")
    if version != ROADMAP_VERSION:
        raise RoadmapError(
            f"unsupported roadmap version {version}; expected version {ROADMAP_VERSION}: {path}"
        )
    fallback = fallback or DEFAULT_FALLBACK
    if fallback != DEFAULT_FALLBACK:
        raise RoadmapError(
            f"unsupported roadmap fallback {fallback!r}; v1 supports only 'oldest': {path}"
        )
    return Roadmap(
        version=version,
        priority=tuple(rules),
        fallback=fallback,
        path=ROADMAP_PATH.as_posix(),
    )


def inspect_existing_run(repo: Path) -> ExistingRun:
    repo = repo.expanduser().resolve()
    current = repo / workflow_stages.CURRENT_DIR
    manifest_path = current / run_manifest.MANIFEST_NAME
    if not manifest_path.is_file():
        return ExistingRun("NONE")
    try:
        manifest = run_manifest.load_manifest(manifest_path)
        state = workflow_stages.read_state(current)
    except (run_manifest.ManifestError, workflow_stages.WorkflowStageError, OSError) as exc:
        return ExistingRun(
            "RUN_HEALTH_BLOCKED",
            reason=f"existing AutoDev run state cannot be read safely: {exc}",
        )

    target = manifest.get("target", {})
    target = target if isinstance(target, dict) else {}
    issue_number = int(target.get("issue_number", state.get("IssueNumber", 0)) or 0)
    branch = str(target.get("branch", state.get("BranchName", "")))
    status = str(state.get("Status", "")).casefold()
    queue_state = str(state.get("QueueState", "")).casefold()
    reason = str(state.get("ExecutionReason", "")).strip()

    if status == "attentionrequired" or queue_state == "attention":
        return ExistingRun(
            "ATTENTION_REQUIRED",
            issue_number=issue_number,
            branch=branch,
            next_stage="execution-classification",
            next_action="human manual/external prerequisite",
            reason=reason or "existing AutoDev run requires human attention",
        )
    if status in {"failed", "blocked"}:
        return ExistingRun(
            "RUN_HEALTH_BLOCKED",
            issue_number=issue_number,
            branch=branch,
            reason=f"existing AutoDev run is terminal/non-runnable with status {state.get('Status', '')}",
        )
    try:
        action = opencode_resume.resume_action(manifest, state)
        next_stage = run_manifest.next_stage(manifest)
    except (run_manifest.ManifestError, opencode_resume.OpenCodeResumeError, ValueError) as exc:
        return ExistingRun(
            "RUN_HEALTH_BLOCKED",
            issue_number=issue_number,
            branch=branch,
            reason=f"existing AutoDev run cannot determine a safe resume boundary: {exc}",
        )
    if action == "complete":
        return ExistingRun("NONE")
    return ExistingRun(
        "RESUME_EXISTING",
        issue_number=issue_number,
        branch=branch,
        next_stage=next_stage,
        next_action=action,
        reason="existing durable AutoDev run takes precedence over unrelated new work",
    )


def active_autodev_prs(
    repo: Path,
    github_repo: str,
    *,
    runner: Callable[..., object] = subprocess.run,
) -> dict[int, str]:
    result = issue_queue._run_gh(  # type: ignore[attr-defined]
        repo,
        [
            "pr",
            "list",
            "--repo",
            github_repo,
            "--state",
            "open",
            "--limit",
            str(issue_queue.DEFAULT_LIMIT),
            "--json",
            "headRefName,url",
        ],
        runner=runner,
    )
    raw = issue_queue._json_result(result, context="gh pr list")  # type: ignore[attr-defined]
    if not isinstance(raw, list):
        raise issue_queue.QueueError("gh pr list did not return an array")
    active: dict[int, str] = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        match = _AUTODEV_BRANCH.match(str(item.get("headRefName", "")))
        if not match:
            continue
        active[int(match.group(1))] = str(item.get("url", ""))
    return active


def _oldest_key(issue: issue_queue.QueueIssue) -> tuple[bool, str, int]:
    return (not bool(issue.created_at), issue.created_at, issue.number)


def _rule_matches(rule: RoadmapRule, issue: issue_queue.QueueIssue) -> bool:
    if rule.kind == "issue":
        return issue.number == rule.value
    if rule.kind == "milestone":
        return bool(issue.milestone) and issue.milestone.casefold() == str(rule.value).casefold()
    return str(rule.value) in issue.labels


def _roadmap_rank(
    roadmap: Roadmap,
    issue: issue_queue.QueueIssue,
) -> tuple[int, int, tuple[bool, str, int], str]:
    issue_rules = [
        rule for rule in roadmap.priority if rule.kind == "issue" and _rule_matches(rule, issue)
    ]
    if issue_rules:
        rule = min(issue_rules, key=lambda item: item.order)
        return (0, rule.order, _oldest_key(issue), "roadmap:issue")
    broad_rules = [
        rule
        for rule in roadmap.priority
        if rule.kind in {"milestone", "label"} and _rule_matches(rule, issue)
    ]
    if broad_rules:
        rule = min(broad_rules, key=lambda item: item.order)
        return (1, rule.order, _oldest_key(issue), f"roadmap:{rule.kind}")
    return (2, len(roadmap.priority), _oldest_key(issue), "oldest")


def _roadmap_ineligible(states: list[issue_queue.QueueState], roadmap: Roadmap) -> tuple[str, ...]:
    messages: list[str] = []
    for rule in roadmap.priority:
        for state in states:
            if state.reason == "ready" or not _rule_matches(rule, state.issue):
                continue
            text = f"roadmap {rule.kind} priority matched #{state.issue.number} but it is {state.reason}"
            if state.reason == "blocked" and state.open_blockers:
                blockers = ", ".join(f"#{item.number}" for item in state.open_blockers)
                text += f" by {blockers}"
            if text not in messages:
                messages.append(text)
    return tuple(messages)


def select_next(
    repo: Path,
    github_repo: str,
    *,
    limit: int = issue_queue.DEFAULT_LIMIT,
    dry_run: bool = False,
    runner: Callable[..., object] = subprocess.run,
    existing_run_inspector: Callable[[Path], ExistingRun] = inspect_existing_run,
    excluded_issue_numbers: frozenset[int] | set[int] = frozenset(),
) -> SelectionResult:
    repo = repo.expanduser().resolve()
    excluded = frozenset(int(item) for item in excluded_issue_numbers if int(item) > 0)
    if dry_run:
        states = issue_queue.inspect_queue(repo, github_repo, limit=limit, runner=runner)
    else:
        states, _created = issue_queue.reconcile_queue(
            repo,
            github_repo,
            limit=limit,
            runner=runner,
        )

    existing = existing_run_inspector(repo)
    if existing.state != "NONE":
        matched = next(
            (state.issue for state in states if state.issue.number == existing.issue_number),
            None,
        )
        if matched is not None and matched.state != "open":
            existing = ExistingRun(
                "RUN_HEALTH_BLOCKED",
                issue_number=existing.issue_number,
                branch=existing.branch,
                reason="existing AutoDev run targets an issue that is now closed",
            )
        return SelectionResult(
            state=existing.state,
            repository=github_repo,
            issue_number=existing.issue_number,
            issue_title=matched.title if matched else "",
            issue_url=matched.url if matched else "",
            source="existing-run",
            explanation=existing.reason,
            next_stage=existing.next_stage,
            next_action=existing.next_action,
            branch=existing.branch,
            dry_run=dry_run,
        )

    roadmap = load_roadmap(repo)
    active_prs = active_autodev_prs(repo, github_repo, runner=runner)
    eligible = [
        state.issue
        for state in states
        if state.reason == "ready"
        and state.issue.number not in active_prs
        and state.issue.number not in excluded
    ]
    ineligible = list(_roadmap_ineligible(states, roadmap))
    for state in states:
        if state.reason == "ready" and state.issue.number in active_prs:
            ineligible.append(
                f"#{state.issue.number} is otherwise ready but already has an active AutoDev PR {active_prs[state.issue.number]}"
            )
        if state.reason == "ready" and state.issue.number in excluded:
            ineligible.append(
                f"#{state.issue.number} is otherwise ready but temporarily excluded by distributed claim ownership"
            )

    if not eligible:
        return SelectionResult(
            state="NO_READY_WORK",
            repository=github_repo,
            source="none",
            explanation="no open managed issue is currently eligible for a new autonomous run",
            roadmap_path=roadmap.path,
            ineligible=tuple(ineligible),
            dry_run=dry_run,
        )

    ranked = sorted(eligible, key=lambda issue: _roadmap_rank(roadmap, issue)[:3])
    winner = ranked[0]
    rank = _roadmap_rank(roadmap, winner)
    source = rank[3]
    if source == "oldest":
        explanation = "oldest eligible issue won the deterministic fallback"
    else:
        explanation = f"eligible issue matched {source.replace(':', ' ')} priority before the oldest fallback"
    return SelectionResult(
        state="SELECTED",
        repository=github_repo,
        issue_number=winner.number,
        issue_title=winner.title,
        issue_url=winner.url,
        source=source,
        explanation=explanation,
        roadmap_path=roadmap.path,
        ineligible=tuple(ineligible),
        dry_run=dry_run,
    )


def render_selection(result: SelectionResult) -> str:
    if result.state == "SELECTED":
        lines = [
            f"SELECTED #{result.issue_number} {result.issue_title}".rstrip(),
            f"source={result.source}",
            f"reason={result.explanation}",
        ]
    elif result.state == "RESUME_EXISTING":
        lines = [
            f"RESUME_EXISTING #{result.issue_number}",
            f"next-stage={result.next_stage or 'unknown'} next-action={result.next_action or 'resume'}",
            f"reason={result.explanation}",
        ]
    elif result.state == "ATTENTION_REQUIRED":
        lines = [
            f"ATTENTION_REQUIRED existing-run #{result.issue_number}",
            f"reason={result.explanation}",
        ]
    elif result.state == "RUN_HEALTH_BLOCKED":
        lines = [
            f"RUN_HEALTH_BLOCKED existing-run #{result.issue_number}",
            f"reason={result.explanation}",
        ]
    else:
        lines = ["NO_READY_WORK", f"reason={result.explanation}"]
    if result.roadmap_path:
        lines.append(f"roadmap={result.roadmap_path}")
    lines.extend(f"skip={message}" for message in result.ineligible)
    if result.dry_run:
        lines.append("dry-run=true")
    return "\n".join(lines)
