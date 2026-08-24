from __future__ import annotations

from automation import windows_verification_manifest

from automation import windows_verification_hooks

from automation import windows_verification_execution

from automation import windows_verification_contract

from automation import semantic_evidence

from pathlib import Path

from automation import opencode_resume_checkpoint, opencode_resume_execution, opencode_resume_status, run_manifest, workflow_stages


WINDOWS_EVIDENCE_FILES = (
    "deferred-verification.json",
    windows_verification_contract.REQUEST_FILE,
    windows_verification_contract.RESULT_FILE,
)
MAX_WINDOWS_EVIDENCE_CHARS = 12_000
_WINDOWS_SEMANTIC_ORDER_INSTALLED = False


def _needs_presemantic_windows(state: dict[str, object]) -> bool:
    return bool(
        state.get("OpenCodeProtocolVersion")
        and state.get("LastLocalCheckPassed")
        and windows_verification_manifest.windows_required(state)
        and not windows_verification_manifest.proof_current(state)
        and str(state.get("LastSemanticVerdict", "")).strip().casefold() != "pass"
    )


def _run_presemantic_windows(
    repo: Path,
    current: Path,
    state: dict[str, object],
    *,
    attempt: int,
    runner,
) -> dict[str, object]:
    """Ship the exact locally verified source and obtain Windows proof before semantic review."""

    max_attempts = workflow_stages.configured_attempt_limit(
        "MAX_REPAIR_ATTEMPTS",
        workflow_stages.DEFAULT_MAX_REPAIR_ATTEMPTS,
    )
    changes = workflow_stages.workspace_changes(repo, current, state)
    if changes:
        workflow_stages.write_json(current / "changed-files.json", changes)
        commit_sha = workflow_stages.create_api_commit(
            repo,
            state,
            changes,
            current,
            runner=runner,
        )
        state = workflow_stages.read_state(current)
        state["LastCommitSha"] = commit_sha
        state["Status"] = "CommittedViaGitHubApi"
        state.pop("CommitTreeBaseSha", None)
        workflow_stages.write_state(current, state)
        snapshot_path = current / "last-commit-workspace-snapshot.json"
        workflow_stages.write_workspace_snapshot(repo, snapshot_path)
        state = workflow_stages.read_state(current)
        state["LastCommitSnapshotHash"] = workflow_stages._file_sha256(snapshot_path)
        workflow_stages.write_state(current, state)
    elif not str(state.get("LastCommitSha", "")).strip():
        raise workflow_stages.WorkflowStageError(
            "required pre-semantic Windows verification has no exact verified source commit to run"
        )

    state = workflow_stages.read_state(current)
    windows = windows_verification_execution.run_after_push(
        repo,
        current,
        state,
        max_repair_attempts=max_attempts,
        runner=runner,
    )
    if windows is None:
        raise workflow_stages.WorkflowStageError(
            "Windows verification is required but the configured Windows lane did not run"
        )
    if windows.get("state") != "CONTINUE":
        return windows

    result = workflow_stages.stage_payload(
        repo,
        "CONTINUE",
        "pr-and-ci",
        next_action="run semantic verification with the current Windows proof",
        max_repair_attempts=max_attempts,
    )
    for key in (
        "artifact",
        "platform_verification_stage",
        "windows_repair_attempt",
        "windows_verification_proof",
        "windows_stage_completed",
    ):
        if key in windows:
            result[key] = windows[key]
    result["platform_verification_only"] = True
    return result


def _preserved_shipped_source_identity(
    repo: Path,
    current: Path,
    state: dict[str, object],
) -> dict[str, object] | None:
    """Reuse the pre-commit verification identity after its exact tree is pushed.

    AutoDev's local checkout intentionally remains on the prepared base while API-created
    commits advance the remote AutoDev branch. Once the pushed tree is proven to be the
    exact locally verified source and the worktree still matches the post-commit snapshot,
    semantic verification must keep using the original verification identity rather than
    reinterpret the same bytes as an empty diff on top of the new remote commit.
    """

    last_commit = str(state.get("LastCommitSha", "")).strip()
    created_commit = str(state.get("CreatedCommitSha", "")).strip()
    verified_parent = str(state.get("VerifiedParentSha", "")).strip()
    created_parent = str(state.get("CreatedParentSha", "")).strip()
    verified_identity = str(state.get("VerifiedSourceIdentity", "")).strip()
    shipped_identity = str(state.get("ShippedSourceIdentity", "")).strip()
    if not (
        last_commit
        and created_commit == last_commit
        and bool(state.get("ShippedTreeVerified"))
        and verified_parent
        and created_parent == verified_parent
        and verified_identity
        and shipped_identity == verified_identity
    ):
        return None

    if workflow_stages.workspace_changes(repo, current, state):
        return None

    raw_changes = state.get("VerifiedChanges", [])
    changes = [dict(item) for item in raw_changes if isinstance(item, dict)] if isinstance(raw_changes, list) else []
    return {
        "parent_sha": verified_parent,
        "identity": verified_identity,
        "changes": changes,
    }


def _with_windows_evidence(current: Path, base: str) -> str:
    parts = [base.rstrip()] if base.strip() else []
    for name in WINDOWS_EVIDENCE_FILES:
        path = current / name
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        parts.append(f"## {name}\n{text[:MAX_WINDOWS_EVIDENCE_CHARS]}")
    return "\n\n".join(part for part in parts if part).strip() or "No deterministic evidence artifact was available."


def install() -> None:
    """Install the OpenCode ordering bridge for required deferred Windows evidence."""

    global _WINDOWS_SEMANTIC_ORDER_INSTALLED

    windows_verification_hooks.install_opencode_hooks()
    if _WINDOWS_SEMANTIC_ORDER_INSTALLED:
        return

    original_execute_stage = workflow_stages.execute_stage
    original_source_identity = workflow_stages.source_identity
    original_resume_action = opencode_resume_status.resume_action
    original_checkpoint_stage = opencode_resume_checkpoint.checkpoint_stage
    original_resume = opencode_resume_execution.resume
    original_collect_deterministic_evidence = semantic_evidence.collect_deterministic_evidence

    def source_identity(
        repo: Path,
        current: Path,
        state: dict[str, object],
    ) -> dict[str, object]:
        preserved = _preserved_shipped_source_identity(repo, current, state)
        if preserved is not None:
            return preserved
        return original_source_identity(repo, current, state)

    def execute_stage(name: str, repo: Path, **kwargs) -> tuple[int, dict[str, object]]:
        resolved = repo.expanduser().resolve()
        current = resolved / workflow_stages.CURRENT_DIR
        if name == "pr-and-ci" and current.is_dir():
            state = workflow_stages.read_state(current)
            if _needs_presemantic_windows(state):
                runner = kwargs.get("runner", workflow_stages.subprocess.run)
                attempt = int(kwargs.get("attempt", 0) or 0)
                return 0, _run_presemantic_windows(
                    resolved,
                    current,
                    state,
                    attempt=attempt,
                    runner=runner,
                )
        return original_execute_stage(name, repo, **kwargs)

    def resume_action(manifest: dict[str, object], state: dict[str, object]) -> str:
        action = original_resume_action(manifest, state)
        if action == "verifier" and _needs_presemantic_windows(state):
            return "pr-and-ci"
        return action

    def checkpoint_stage(
        repo: Path,
        name: str,
        payload: dict[str, object],
        attempt: int,
    ) -> None:
        if name == "pr-and-ci" and payload.get("platform_verification_only"):
            # The existing Windows checkpoint hook already knows how to persist the
            # windows-verified optional stage. Marking this checkpoint internally as
            # the Windows boundary prevents it from also completing pr-created.
            windows_payload = dict(payload)
            windows_payload["failed_stage"] = "windows-verification"
            original_checkpoint_stage(repo, name, windows_payload, attempt)
            return
        original_checkpoint_stage(repo, name, payload, attempt)

    def resume(repo: Path, mappings: dict[str, dict[str, str]], **kwargs) -> dict[str, object]:
        payload = original_resume(repo, mappings, **kwargs)
        current = repo.expanduser().resolve() / workflow_stages.CURRENT_DIR
        state = workflow_stages.read_state(current)
        if payload.get("next_action") == "pr-and-ci" and _needs_presemantic_windows(state):
            payload["next_stage"] = windows_verification_contract.MANIFEST_STAGE
        return payload

    def collect_deterministic_evidence(current: Path) -> str:
        return _with_windows_evidence(
            current,
            original_collect_deterministic_evidence(current),
        )

    workflow_stages.source_identity = source_identity
    workflow_stages.execute_stage = execute_stage
    opencode_resume_status.resume_action = resume_action
    opencode_resume_checkpoint.checkpoint_stage = checkpoint_stage
    opencode_resume_execution.resume = resume
    semantic_evidence.collect_deterministic_evidence = collect_deterministic_evidence
    _WINDOWS_SEMANTIC_ORDER_INSTALLED = True
