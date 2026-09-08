from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from automation import continuation, run_manifest, workflow_contract, workflow_storage
from automation.workflow_commands import git


PENDING_FILE = "continuation-pending.json"


def _current(repo: Path) -> Path:
    return repo.expanduser().resolve() / workflow_contract.CURRENT_DIR


def _pending_path(repo: Path) -> Path:
    return _current(repo) / PENDING_FILE


def _load_pending(repo: Path) -> dict[str, object]:
    value = workflow_storage.read_json(_pending_path(repo))
    return value if isinstance(value, dict) else {}


def _head(repo: Path, *, runner: Callable[..., object]) -> str:
    completed = git(repo, ["rev-parse", "HEAD"], runner=runner)
    return str(getattr(completed, "stdout", "") or "").strip()


def _validate_before_pending(
    repo: Path,
    resolved_sha: str,
    *,
    runner: Callable[..., object],
) -> None:
    current = _current(repo)
    state_value = workflow_storage.read_json(current / "state.json")
    state = state_value if isinstance(state_value, dict) else {}
    if not state or not (current / run_manifest.MANIFEST_NAME).is_file():
        raise continuation.ContinuationError(
            "--continue-from on resume requires an existing durable AutoDev run"
        )
    base_sha = str(state.get("BaseSha", "")).strip()
    if not base_sha:
        raise continuation.ContinuationError(
            "current run is missing its configured development-base SHA"
        )
    continuation._require_policy_ancestry(
        repo,
        base_sha,
        resolved_sha,
        runner=runner,
    )
    dirty = continuation._dirty_paths(repo, runner=runner)
    if dirty:
        raise continuation.ContinuationError(
            "cannot adopt continuation source with a dirty worktree; commit/stash the current changes first: "
            + ", ".join(dirty[:20])
        )


def _persist_requested_identity(
    repo: Path,
    *,
    requested_ref: str,
    resolved_sha: str,
) -> None:
    current = _current(repo)
    state_path = current / "state.json"
    state_value = workflow_storage.read_json(state_path)
    state = state_value if isinstance(state_value, dict) else {}
    state["ContinuationRequestedRef"] = requested_ref
    state["ContinuationResolvedSha"] = resolved_sha
    workflow_storage.write_json(state_path, state)

    manifest_path = current / run_manifest.MANIFEST_NAME
    manifest = run_manifest.load_manifest(manifest_path)
    record = manifest.get("continuation_source", {})
    if not isinstance(record, dict):
        record = {}
    record["schema_version"] = continuation.SCHEMA_VERSION
    record["requested_ref"] = requested_ref
    record["resolved_sha"] = resolved_sha
    manifest["continuation_source"] = record
    run_manifest.save_manifest(manifest_path, manifest)


def _already_adopted(
    repo: Path,
    resolved_sha: str,
    *,
    runner: Callable[..., object],
) -> bool:
    current = _current(repo)
    state_value = workflow_storage.read_json(current / "state.json")
    state = state_value if isinstance(state_value, dict) else {}
    if (
        str(state.get("ContinuationResolvedSha", "")).strip() != resolved_sha
        or _head(repo, runner=runner) != resolved_sha
    ):
        return False

    try:
        manifest = run_manifest.load_manifest(current / run_manifest.MANIFEST_NAME)
    except run_manifest.ManifestError:
        return False
    record = manifest.get("continuation_source", {})
    target = manifest.get("target", {})
    if not isinstance(record, dict) or not isinstance(target, dict):
        return False
    return (
        str(record.get("resolved_sha", "")).strip() == resolved_sha
        and str(target.get("branch", "")).strip()
        == str(state.get("BranchName", "")).strip()
    )


def adopt(
    repo: Path,
    requested_ref: str,
    *,
    runner: Callable[..., object] = subprocess.run,
) -> dict[str, object]:
    """Begin an existing-run continuation transaction and finish it synchronously.

    The pending record is persisted before the checkout/adoption mutation. If the
    process disappears afterward, an ordinary public resume can call
    ``finish_pending`` and deterministically complete the same immutable SHA.
    """

    repo = repo.expanduser().resolve()
    pending = _load_pending(repo)
    if pending:
        existing_requested = str(pending.get("requested_ref", "")).strip()
        existing_sha = str(pending.get("resolved_sha", "")).strip()
        raise continuation.ContinuationError(
            "a continuation adoption is already pending"
            + (
                f" for {existing_requested!r} -> {existing_sha}; run `autodev resume` without a new --continue-from to finish it"
                if existing_requested or existing_sha
                else "; run `autodev resume` without a new --continue-from to finish it"
            )
        )

    resolved = continuation.resolve_ref(repo, requested_ref, runner=runner)
    _validate_before_pending(repo, resolved, runner=runner)
    record: dict[str, object] = {
        "schema_version": continuation.SCHEMA_VERSION,
        "requested_ref": requested_ref,
        "resolved_sha": resolved,
        "prepared_at": datetime.now(timezone.utc).isoformat(),
    }
    workflow_storage.write_json(_pending_path(repo), record)
    return finish_pending(repo, runner=runner)


def finish_pending(
    repo: Path,
    *,
    runner: Callable[..., object] = subprocess.run,
) -> dict[str, object]:
    repo = repo.expanduser().resolve()
    pending = _load_pending(repo)
    if not pending:
        return {}
    if int(pending.get("schema_version", 0) or 0) != continuation.SCHEMA_VERSION:
        raise continuation.ContinuationError(
            f"unsupported pending continuation schema {pending.get('schema_version')}"
        )
    requested = str(pending.get("requested_ref", "")).strip()
    resolved = str(pending.get("resolved_sha", "")).strip()
    if not requested or not resolved:
        raise continuation.ContinuationError(
            "pending continuation record is missing requested_ref/resolved_sha"
        )

    if not _already_adopted(repo, resolved, runner=runner):
        # Pass the immutable SHA to the core adopter so a moved branch/tag can
        # never change the transaction after the pending boundary was written.
        continuation.adopt_existing_run(repo, resolved, runner=runner)

    _persist_requested_identity(
        repo,
        requested_ref=requested,
        resolved_sha=resolved,
    )
    _pending_path(repo).unlink(missing_ok=True)
    return {
        "schema_version": continuation.SCHEMA_VERSION,
        "requested_ref": requested,
        "resolved_sha": resolved,
    }
