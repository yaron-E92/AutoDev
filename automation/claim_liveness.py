from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from automation import run_manifest
from automation.claim_process import _git, _returncode, _stdout
from automation.workflow_contract import CURRENT_DIR


@dataclass(frozen=True)
class ProgressSnapshot:
    identity: str
    summary: str
    terminal: bool = False


_STATE_EXACT_KEYS = {
    "Status",
    "QueueState",
    "IssueNumber",
    "DevelopmentStrategy",
    "IntegrationBranch",
    "ReleaseBranch",
    "Base",
    "BranchName",
    "BaseSha",
    "BaseTreeSha",
    "PreparedLocalHeadSha",
    "PreparedSnapshotHash",
    "LastCommitSha",
    "PrNumber",
    "PrHeadSha",
    "LastLocalCheckPassed",
    "LastWindowsCheckPassed",
    "LastSemanticCheckPassed",
}

_STATE_SUFFIXES = (
    "sha",
    "hash",
    "fingerprint",
    "attempt",
    "attempts",
    "count",
    "counter",
)

_TERMINAL_STATES = {
    "readyforreview",
    "prready",
    "attentionrequired",
    "attention",
    "blocked",
    "failed",
    "terminalfailed",
}

_UNSAFE = object()


def _read_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _safe_scalar(value: object) -> object:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        safe = [_safe_scalar(item) for item in value]
        return safe if all(item is not _UNSAFE for item in safe) else _UNSAFE
    return _UNSAFE


def _state_progress(state: dict[str, object], issue_number: int) -> dict[str, object]:
    state_issue = int(state.get("IssueNumber", 0) or 0)
    if state_issue and state_issue != issue_number:
        return {"issue_number": issue_number, "durable_state": "different-issue"}

    progress: dict[str, object] = {"issue_number": issue_number}
    for key, value in sorted(state.items()):
        normalized = key.casefold().replace("-", "").replace("_", "")
        allowed = key in _STATE_EXACT_KEYS or normalized.endswith(_STATE_SUFFIXES)
        if not allowed:
            continue
        safe = _safe_scalar(value)
        if safe is not _UNSAFE:
            progress[key] = safe
    return progress


def _manifest_stage_progress(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, object] = {}
    for key in ("status", "input_hash", "output_hash"):
        safe = _safe_scalar(value.get(key))
        if safe is not _UNSAFE and safe not in ("", None):
            result[key] = safe
    artifacts = value.get("artifacts")
    if isinstance(artifacts, dict):
        result["artifacts"] = {
            str(path): str(digest)
            for path, digest in sorted(artifacts.items(), key=lambda pair: str(pair[0]))
            if isinstance(digest, str)
        }
    return result


def _manifest_progress(manifest: dict[str, object], issue_number: int) -> dict[str, object]:
    if not manifest:
        return {}
    target = manifest.get("target")
    if not isinstance(target, dict):
        return {}
    target_issue = int(target.get("issue_number", 0) or 0)
    if target_issue and target_issue != issue_number:
        return {}

    result: dict[str, object] = {
        "run_id": str(manifest.get("run_id", "") or ""),
        "current_stage": str(manifest.get("current_stage", "") or ""),
    }
    completed = manifest.get("completed_stages")
    if isinstance(completed, list):
        result["completed_stages"] = [str(item) for item in completed]

    safe_target: dict[str, object] = {}
    for key in ("issue_number", "mode", "base_sha", "branch"):
        safe = _safe_scalar(target.get(key))
        if safe is not _UNSAFE and safe not in ("", None):
            safe_target[key] = safe
    if safe_target:
        result["target"] = safe_target

    stages = manifest.get("stages")
    if isinstance(stages, dict):
        safe_stages = {
            str(stage): _manifest_stage_progress(record)
            for stage, record in sorted(stages.items(), key=lambda pair: str(pair[0]))
        }
        result["stages"] = {
            stage: record for stage, record in safe_stages.items() if record
        }

    failure = manifest.get("failure")
    if isinstance(failure, dict):
        safe_failure: dict[str, object] = {}
        for key, value in sorted(failure.items()):
            normalized = str(key).casefold().replace("-", "").replace("_", "")
            if normalized in {"classification", "kind", "state"} or normalized.endswith(
                _STATE_SUFFIXES
            ):
                safe = _safe_scalar(value)
                if safe is not _UNSAFE and safe not in ("", None):
                    safe_failure[str(key)] = safe
        if safe_failure:
            result["failure"] = safe_failure

    pr = manifest.get("pr")
    if isinstance(pr, dict):
        safe_pr: dict[str, object] = {}
        for key, value in sorted(pr.items()):
            normalized = str(key).casefold().replace("-", "").replace("_", "")
            if normalized in {"number", "state", "status"} or normalized.endswith("sha"):
                safe = _safe_scalar(value)
                if safe is not _UNSAFE and safe not in ("", None):
                    safe_pr[str(key)] = safe
        if safe_pr:
            result["pr"] = safe_pr
    return result


def _branch_head(
    repo: Path,
    branch: str,
    *,
    runner: Callable[..., object],
) -> dict[str, str]:
    if not branch:
        return {}
    result: dict[str, str] = {}
    for label, ref in (
        ("local", f"refs/heads/{branch}"),
        ("remote", f"refs/remotes/origin/{branch}"),
    ):
        completed = _git(
            repo,
            ["rev-parse", "--verify", ref],
            runner=runner,
            check=False,
        )
        if _returncode(completed) == 0:
            sha = _stdout(completed).strip()
            if sha:
                result[label] = sha
    return result


def _normalized_state(value: object) -> str:
    return str(value or "").casefold().replace("_", "").replace("-", "")


def progress_snapshot(
    repo: Path,
    issue_number: int,
    *,
    runner: Callable[..., object] = subprocess.run,
) -> ProgressSnapshot:
    repo = repo.expanduser().resolve()
    current = repo / CURRENT_DIR
    state = _read_json(current / "state.json")
    manifest = _read_json(current / run_manifest.MANIFEST_NAME)

    state_progress = _state_progress(state, issue_number)
    manifest_progress = _manifest_progress(manifest, issue_number)
    branch = str(state.get("BranchName", "") or "")
    branch_head = _branch_head(repo, branch, runner=runner)

    payload = {
        "schema": 1,
        "issue_number": issue_number,
        "state": state_progress,
        "manifest": manifest_progress,
        "branch_head": branch_head,
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    identity = hashlib.sha256(encoded).hexdigest()

    status = str(
        state.get("Status", "")
        or state.get("QueueState", "")
        or manifest.get("current_stage", "")
        or "none"
    )
    completed = manifest_progress.get("completed_stages", [])
    completed_count = len(completed) if isinstance(completed, list) else 0
    head = branch_head.get("remote") or branch_head.get("local") or str(
        state.get("LastCommitSha", "") or ""
    )
    pr_number = int(state.get("PrNumber", 0) or 0)
    summary = (
        f"status={status}; completed={completed_count}; "
        f"head={(head[:12] if head else 'none')}; "
        f"pr={(pr_number if pr_number > 0 else 'none')}; progress={identity[:12]}"
    )
    terminal = _normalized_state(status) in _TERMINAL_STATES
    return ProgressSnapshot(identity=identity, summary=summary[:240], terminal=terminal)
