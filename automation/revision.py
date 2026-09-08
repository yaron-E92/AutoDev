from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from automation import run_manifest, semver_intent, workflow_stages
from automation import workflow_commands


SCHEMA_VERSION = 1
ACTIVE_REVISION_FILE = "revision.json"
REVISIONS_DIR = "revisions"
MAX_INSTRUCTIONS_CHARS = 20_000
REVISION_ROLES = ("synthesizer", "planner", "implementer", "fixer", "verifier")
SUPERSEDED_ARTIFACTS = (
    "synthesized-handoff.md",
    "plan.md",
    "commit-message.txt",
    "verification-result.json",
)


class RevisionError(ValueError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _current(repo: Path) -> Path:
    return repo.expanduser().resolve() / workflow_stages.CURRENT_DIR


def active_path(repo: Path) -> Path:
    return _current(repo) / ACTIVE_REVISION_FILE


def _revision_root(repo: Path, revision_id: str) -> Path:
    return _current(repo) / REVISIONS_DIR / revision_id


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()


def _read_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.write_text(
            json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise RevisionError(f"cannot persist revision state {path}: {exc}") from exc


def load_active(repo: Path) -> dict[str, object]:
    path = active_path(repo)
    value = _read_json(path)
    if not value:
        return {}
    if value.get("schema_version") != SCHEMA_VERSION:
        raise RevisionError(
            f"unsupported revision schema version {value.get('schema_version')}; expected {SCHEMA_VERSION}"
        )
    if not str(value.get("revision_id", "")).strip():
        raise RevisionError("revision state is missing revision_id")
    return value


def _copy_if_present(source: Path, target: Path) -> str:
    if not source.is_file():
        return ""
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return target.name


def _git_stdout(
    repo: Path,
    arguments: list[str],
    *,
    runner: Callable[..., object],
) -> str:
    try:
        completed = workflow_stages.git(repo, arguments, runner=runner)
    except workflow_stages.WorkflowStageError as exc:
        raise RevisionError(str(exc)) from exc
    return str(getattr(completed, "stdout", "") or "").strip()


def _issue_text(issue_number: int, issue: dict[str, object]) -> str:
    title = str(issue.get("title", "")).strip()
    url = str(issue.get("url", "")).strip()
    body = str(issue.get("body", "") or "")
    return f"# GitHub Issue #{issue_number}: {title}\n\nURL: {url}\n\n{body}\n"


def _fetch_issue(
    repo: Path,
    state: dict[str, object],
    *,
    runner: Callable[..., object],
) -> dict[str, object]:
    issue_number = int(state.get("IssueNumber", 0) or 0)
    repo_full = str(state.get("RepoFullName", "")).strip()
    if issue_number <= 0 or not repo_full:
        raise RevisionError("current run is missing GitHub repository/issue identity")
    try:
        issue = workflow_commands.gh_json(
            repo,
            [
                "issue",
                "view",
                str(issue_number),
                "--repo",
                repo_full,
                "--json",
                "number,title,body,url,labels",
            ],
            runner=runner,
        )
    except workflow_stages.WorkflowStageError as exc:
        raise RevisionError(f"cannot refresh GitHub issue #{issue_number}: {exc}") from exc
    if not isinstance(issue, dict):
        raise RevisionError(f"GitHub issue #{issue_number} did not return an object")
    return issue


def _manual_instructions(
    *,
    instructions: str,
    instructions_file: Path | None,
) -> tuple[str, str]:
    inline = str(instructions or "").strip()
    if inline and instructions_file is not None:
        raise RevisionError("use either --instructions or --instructions-file, not both")
    source = ""
    text = inline
    if instructions_file is not None:
        path = instructions_file.expanduser().resolve()
        try:
            text = path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise RevisionError(f"cannot read revision instructions file {path}: {exc}") from exc
        source = str(path)
    elif inline:
        source = "inline"
    if len(text) > MAX_INSTRUCTIONS_CHARS:
        raise RevisionError(
            f"revision instructions exceed the {MAX_INSTRUCTIONS_CHARS}-character limit"
        )
    return text, source


def _archive_superseded(current: Path, revision_root: Path) -> dict[str, str]:
    archived: dict[str, str] = {}
    for name in SUPERSEDED_ARTIFACTS:
        copied = _copy_if_present(current / name, revision_root / "superseded" / name)
        if copied:
            archived[name] = f"superseded/{copied}"
    final_verdict = current / "verification" / "final-verdict.json"
    copied = _copy_if_present(
        final_verdict,
        revision_root / "superseded" / "verification-final-verdict.json",
    )
    if copied:
        archived["verification/final-verdict.json"] = f"superseded/{copied}"
    return archived


def _clear_downstream_state(state: dict[str, object], revision_id: str) -> None:
    accepted = state.get("AcceptedRoleArtifacts", {})
    if isinstance(accepted, dict):
        for role in REVISION_ROLES:
            accepted.pop(role, None)
        state["AcceptedRoleArtifacts"] = accepted
    state["Status"] = "Prepared"
    state["RevisionId"] = revision_id
    state["RevisionStage"] = "synthesizer"
    state["LastLocalCheckPassed"] = False
    state["PrHeadSha"] = ""
    state["CiProof"] = {}
    for key in (
        "VerifiedParentSha",
        "VerifiedSourceIdentity",
        "ShippedSourceIdentity",
        "LastCommitSnapshotHash",
    ):
        state[key] = ""
    state["VerifiedChanges"] = []


def _refresh_issue_checkpoint(
    current: Path,
    manifest_path: Path,
    *,
    revision_id: str,
    previous_sha: str,
    refreshed_sha: str,
) -> None:
    manifest = run_manifest.load_manifest(manifest_path)
    stages = manifest.get("stages", {})
    record = stages.get("issue-selected", {}) if isinstance(stages, dict) else {}
    details = record.get("details", {}) if isinstance(record, dict) else {}
    try:
        run_manifest.complete_stage(
            manifest_path,
            "issue-selected",
            run_root=current,
            artifacts=[current / "issue.md"],
            inputs={
                "revision_id": revision_id,
                "previous_issue_sha256": previous_sha,
                "issue_sha256": refreshed_sha,
            },
            details={
                **(dict(details) if isinstance(details, dict) else {}),
                "issue_refreshed_by_revision": revision_id,
                "previous_issue_sha256": previous_sha,
                "issue_sha256": refreshed_sha,
            },
        )
    except run_manifest.ManifestError as exc:
        raise RevisionError(str(exc)) from exc


def begin_revision(
    repo: Path,
    *,
    instructions: str = "",
    instructions_file: Path | None = None,
    refresh_issue: bool = False,
    runner: Callable[..., object] = subprocess.run,
) -> dict[str, object]:
    repo = repo.expanduser().resolve()
    current = _current(repo)
    manifest_path = current / run_manifest.MANIFEST_NAME
    if not current.is_dir() or not manifest_path.is_file():
        raise RevisionError(
            ".autodev-run/current/run-manifest.json is missing; revision requires an existing resumable run"
        )

    manual_text, manual_source = _manual_instructions(
        instructions=instructions,
        instructions_file=instructions_file,
    )
    if not manual_text and not refresh_issue:
        raise RevisionError(
            "revision requires --instructions, --instructions-file, --refresh-issue, or a combination with --refresh-issue"
        )

    existing_active = load_active(repo)
    if existing_active and str(existing_active.get("status", "active")) == "active":
        raise RevisionError(
            f"revision {existing_active.get('revision_id')} is already active; resume it before starting another revision"
        )

    state = _read_json(current / "state.json")
    if not state:
        raise RevisionError("current run state.json is missing or invalid")
    try:
        manifest = run_manifest.load_manifest(manifest_path)
    except run_manifest.ManifestError as exc:
        raise RevisionError(str(exc)) from exc

    revision_id = (
        datetime.now(timezone.utc).strftime("r%Y%m%dT%H%M%SZ-")
        + uuid.uuid4().hex[:8]
    )
    revision_root = _revision_root(repo, revision_id)
    revision_root.mkdir(parents=True, exist_ok=False)

    _copy_if_present(current / "state.json", revision_root / "state-before.json")
    _copy_if_present(current / "issue.md", revision_root / "issue-before.md")
    archived = _archive_superseded(current, revision_root)

    issue_path = current / "issue.md"
    previous_issue_text = issue_path.read_text(encoding="utf-8") if issue_path.is_file() else str(state.get("IssueText", ""))
    previous_issue_sha = _sha256_text(previous_issue_text)
    refreshed_issue_sha = previous_issue_sha
    issue_changed = False

    if refresh_issue:
        issue = _fetch_issue(repo, state, runner=runner)
        refreshed_text = _issue_text(int(state.get("IssueNumber", 0) or 0), issue)
        refreshed_issue_sha = _sha256_text(refreshed_text)
        issue_changed = refreshed_issue_sha != previous_issue_sha
        issue_path.write_text(refreshed_text, encoding="utf-8")
        state["IssueText"] = refreshed_text
        state["IssueTitle"] = str(issue.get("title", "")).strip()
        state["IssueUrl"] = str(issue.get("url", "")).strip()
        labels = issue.get("labels", [])
        state["Labels"] = [
            str(item.get("name", ""))
            for item in labels
            if isinstance(item, dict) and str(item.get("name", "")).strip()
        ] if isinstance(labels, list) else []
        if str(state.get("SemVerIntentSource", "")) != "explicit":
            resolved = semver_intent.resolve_intent(
                refreshed_text,
                repository_default=semver_intent.repository_default(repo),
            )
            state["SemVerIntent"] = resolved.intent
            state["SemVerIntentSource"] = resolved.source
        _refresh_issue_checkpoint(
            current,
            manifest_path,
            revision_id=revision_id,
            previous_sha=previous_issue_sha,
            refreshed_sha=refreshed_issue_sha,
        )

    try:
        head_sha = _git_stdout(repo, ["rev-parse", "HEAD"], runner=runner)
        source = workflow_stages.source_identity(repo, current, state)
    except workflow_stages.WorkflowStageError as exc:
        raise RevisionError(str(exc)) from exc
    if not isinstance(source, dict):
        source = {}

    if manual_text:
        (revision_root / "instructions.md").write_text(manual_text + "\n", encoding="utf-8")

    try:
        superseded_stages = run_manifest.invalidate_role(
            manifest_path,
            "synthesizer",
            reason=f"operator-directed revision {revision_id}",
        )
    except run_manifest.ManifestError as exc:
        raise RevisionError(str(exc)) from exc

    _clear_downstream_state(state, revision_id)
    _write_json(current / "state.json", state)

    roles = manifest.get("roles", {})
    role_fingerprints = {
        str(role): str(snapshot.get("fingerprint", ""))
        for role, snapshot in roles.items()
        if isinstance(roles, dict) and isinstance(snapshot, dict)
    }
    trigger = (
        "manual+issue-refresh"
        if manual_text and refresh_issue
        else ("manual" if manual_text else "issue-refresh")
    )
    record: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "revision_id": revision_id,
        "status": "active",
        "trigger": trigger,
        "created_at": _utc_now(),
        "updated_at": _utc_now(),
        "run_id": str(manifest.get("run_id", "")),
        "issue_number": int(state.get("IssueNumber", 0) or 0),
        "github_repo": str(state.get("RepoFullName", "")),
        "branch": str(state.get("BranchName", "")),
        "head_sha": head_sha,
        "implementation_source_identity": str(source.get("identity", "")),
        "implementation_parent_sha": str(source.get("parent_sha", "")),
        "implementation_changes": source.get("changes", []) if isinstance(source.get("changes", []), list) else [],
        "previous_issue_sha256": previous_issue_sha,
        "refreshed_issue_sha256": refreshed_issue_sha if refresh_issue else "",
        "issue_changed": issue_changed,
        "instructions_sha256": _sha256_text(manual_text) if manual_text else "",
        "instructions_source": manual_source,
        "instructions_artifact": "instructions.md" if manual_text else "",
        "superseded_stages": superseded_stages,
        "archived_artifacts": archived,
        "role_fingerprints_at_start": role_fingerprints,
        "revised_role_fingerprints": {},
        "current_revised_stage": "synthesizer",
        "next_action": "resume",
    }
    _write_json(revision_root / "record.json", record)
    _write_json(active_path(repo), record)
    return record


def _instructions_for(repo: Path, record: dict[str, object]) -> str:
    relative = str(record.get("instructions_artifact", "")).strip()
    if not relative:
        return ""
    path = _revision_root(repo, str(record.get("revision_id", ""))) / relative
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise RevisionError(f"cannot read active revision instructions {path}: {exc}") from exc


def role_prompt_context(repo: Path, role: str) -> str:
    record = load_active(repo)
    if not record or str(record.get("status", "")) != "active":
        return ""
    if role not in {"synthesizer", "planner", "implementer"}:
        return ""

    instructions = _instructions_for(repo, record)
    header = (
        "\n\n# AutoDev operator-directed revision\n\n"
        f"Revision: {record.get('revision_id')}\n"
        f"Trigger: {record.get('trigger')}\n"
        "The existing implementation/worktree is intentional revision input. Preserve compatible work; "
        "do not reset to the prepared base merely because earlier plan/checkpoint evidence was superseded.\n"
    )
    if instructions:
        header += (
            "\nOperator revision instructions (authoritative):\n\n"
            "```text\n"
            + instructions
            + "\n```\n"
        )

    if role == "synthesizer":
        return header + (
            "\nReconcile the current authoritative `.autodev-run/current/issue.md`, the operator instructions, "
            "the existing repository Reader evidence, prior artifacts in the revision archive where useful, "
            "and the current implementation/worktree. Produce the revised synthesis for the implementation as it "
            "exists now, not a greenfield restatement. If the refreshed issue and operator instructions are "
            "irreconcilably contradictory, do not silently choose one: include a line beginning exactly "
            "`REVISION_CONFLICT:` followed by a bounded explanation.\n"
        )
    if role == "planner":
        return header + (
            "\nThis must be a delta plan against the current implementation. Keep the normal exact six-section "
            "Planner contract, but make section 4 explicitly identify what to PRESERVE, REMOVE/REVERT, CHANGE, "
            "and ADD. Section 5 must identify verification obligations introduced by the revision. Do not plan a "
            "greenfield rewrite unless the authoritative revision actually requires one.\n"
        )
    return header + (
        "\nApply the delta plan to the current implementation in place. Preserve compatible existing work, remove "
        "or change only what the revised plan requires, and do not reset/recreate the issue implementation from "
        "scratch. After the delta is applied, normal deterministic and semantic verification will run.\n"
    )


def validate_synthesizer_result(repo: Path, text: str) -> None:
    record = load_active(repo)
    if not record or str(record.get("status", "")) != "active":
        return
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if not stripped.startswith("REVISION_CONFLICT:"):
            continue
        detail = stripped.split(":", 1)[1].strip()
        if detail and detail.casefold() not in {"none", "no conflict", "none detected"}:
            raise RevisionError(
                f"revision {record.get('revision_id')} has an unresolved authority conflict: {detail}"
            )


def record_role_use(repo: Path, role: str, snapshot: object) -> None:
    record = load_active(repo)
    if not record or str(record.get("status", "")) != "active" or role not in REVISION_ROLES:
        return
    fingerprint = str(snapshot.get("fingerprint", "")) if isinstance(snapshot, dict) else ""
    revised = record.get("revised_role_fingerprints", {})
    revised = dict(revised) if isinstance(revised, dict) else {}
    revised[role] = fingerprint
    record["revised_role_fingerprints"] = revised
    next_stage = {
        "synthesizer": "planner",
        "planner": "implementer",
        "implementer": "deterministic-verification",
        "fixer": "deterministic-verification",
        "verifier": "shipment",
    }.get(role, role)
    record["current_revised_stage"] = next_stage
    record["updated_at"] = _utc_now()
    _write_json(active_path(repo), record)
    _write_json(_revision_root(repo, str(record.get("revision_id", ""))) / "record.json", record)

    current = _current(repo)
    state = _read_json(current / "state.json")
    if state:
        state["RevisionStage"] = next_stage
        _write_json(current / "state.json", state)


def mark_complete(repo: Path) -> None:
    record = load_active(repo)
    if not record or str(record.get("status", "")) != "active":
        return
    record["status"] = "complete"
    record["current_revised_stage"] = "complete"
    record["next_action"] = "review"
    record["completed_at"] = _utc_now()
    record["updated_at"] = record["completed_at"]
    _write_json(active_path(repo), record)
    _write_json(_revision_root(repo, str(record.get("revision_id", ""))) / "record.json", record)

    state = _read_json(_current(repo) / "state.json")
    if state:
        state["RevisionStage"] = "complete"
        _write_json(_current(repo) / "state.json", state)


def status(repo: Path) -> dict[str, object]:
    record = load_active(repo)
    if not record:
        return {"present": False}
    return {
        "present": True,
        "revision_id": str(record.get("revision_id", "")),
        "status": str(record.get("status", "")),
        "trigger": str(record.get("trigger", "")),
        "current_revised_stage": str(record.get("current_revised_stage", "")),
        "next_action": str(record.get("next_action", "")),
        "issue_changed": bool(record.get("issue_changed", False)),
        "superseded_stages": list(record.get("superseded_stages", []))
        if isinstance(record.get("superseded_stages", []), list)
        else [],
    }
