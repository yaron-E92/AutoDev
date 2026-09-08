from __future__ import annotations

from pathlib import Path

from automation import (
    opencode_adapter_protocol,
    opencode_adapter_roles,
    opencode_resume_status,
    revision,
    role_coordinator_flow,
    run_manifest,
    workflow_stages,
)


PRE_PATCH_WORKTREE_BLOCKER = "worktree changed before the patch-applied checkpoint"


def _append_revision_prompt(path: Path, context: str) -> None:
    if not context:
        return
    try:
        existing = path.read_text(encoding="utf-8")
        path.write_text(existing.rstrip() + context + "\n", encoding="utf-8")
    except OSError as exc:
        raise revision.RevisionError(
            f"cannot bind active revision context to role prompt {path}: {exc}"
        ) from exc


def _record_role(repo: Path, role: str) -> None:
    active = revision.load_active(repo)
    if not active or str(active.get("status", "")) != "active":
        return
    manifest_path = repo / workflow_stages.CURRENT_DIR / run_manifest.MANIFEST_NAME
    try:
        manifest = run_manifest.load_manifest(manifest_path)
    except run_manifest.ManifestError as exc:
        raise revision.RevisionError(str(exc)) from exc
    roles = manifest.get("roles", {})
    snapshot = roles.get(role, {}) if isinstance(roles, dict) else {}
    revision.record_role_use(repo, role, snapshot)


def _revision_status_text(repo: Path) -> str:
    summary = revision.status(repo)
    if not bool(summary.get("present")):
        return ""
    superseded = summary.get("superseded_stages", [])
    superseded_text = (
        ", ".join(str(value) for value in superseded)
        if isinstance(superseded, list) and superseded
        else "(none)"
    )
    return (
        f"Revision: {summary.get('revision_id', '')} | "
        f"trigger={summary.get('trigger', '')} | status={summary.get('status', '')}\n"
        f"Revision stage: {summary.get('current_revised_stage', '')} | "
        f"next={summary.get('next_action', '')}\n"
        f"Revision superseded stages: {superseded_text}\n"
    )


def _revision_source_is_unchanged(repo: Path, active: dict[str, object]) -> bool:
    expected = str(active.get("implementation_source_identity", "")).strip()
    if not expected:
        return False
    current = repo / workflow_stages.CURRENT_DIR
    state = workflow_stages.read_state(current)
    try:
        source = workflow_stages.source_identity(repo, current, state)
    except workflow_stages.WorkflowStageError:
        return False
    return str(source.get("identity", "")).strip() == expected


def install() -> None:
    current_prepare = opencode_adapter_roles.prepare_role
    if not getattr(current_prepare, "_autodev_revision", False):
        original_prepare = current_prepare

        def prepare_role(role: str, repo: Path, arguments: str, **kwargs):
            resolved = Path(repo).expanduser().resolve()
            path = original_prepare(role, resolved, arguments, **kwargs)
            _append_revision_prompt(path, revision.role_prompt_context(resolved, role))
            return path

        prepare_role._autodev_revision = True  # type: ignore[attr-defined]
        opencode_adapter_roles.prepare_role = prepare_role

    current_accept_once = opencode_adapter_roles._accept_role_once
    if not getattr(current_accept_once, "_autodev_revision", False):
        original_accept_once = current_accept_once

        def accept_role_once(role: str, current: Path, input_path: Path | None):
            outputs = original_accept_once(role, current, input_path)
            if role == "synthesizer":
                repo = current.expanduser().resolve().parent.parent
                handoff = current / "synthesized-handoff.md"
                text = handoff.read_text(encoding="utf-8") if handoff.is_file() else ""
                revision.validate_synthesizer_result(repo, text)
            return outputs

        accept_role_once._autodev_revision = True  # type: ignore[attr-defined]
        opencode_adapter_roles._accept_role_once = accept_role_once

    current_mark = opencode_adapter_protocol._mark_role_accepted
    if not getattr(current_mark, "_autodev_revision", False):
        original_mark = current_mark

        def mark_role_accepted(current: Path, role: str, outputs: list[Path]) -> None:
            original_mark(current, role, outputs)
            repo = current.expanduser().resolve().parent.parent
            _record_role(repo, role)

        mark_role_accepted._autodev_revision = True  # type: ignore[attr-defined]
        opencode_adapter_protocol._mark_role_accepted = mark_role_accepted
        # opencode_adapter_roles imports this helper by name, so update that
        # module-level alias as well to keep direct `autodev accept` behavior equal.
        opencode_adapter_roles._mark_role_accepted = mark_role_accepted

    current_problems = opencode_resume_status._resume_problems
    if not getattr(current_problems, "_autodev_revision", False):
        original_problems = current_problems

        def resume_problems(repo: Path, current: Path, manifest, state, **kwargs):
            resolved = Path(repo).expanduser().resolve()
            problems = list(
                original_problems(resolved, current, manifest, state, **kwargs)
            )
            active = revision.load_active(resolved)
            if not active or str(active.get("status", "")) != "active":
                return problems
            if run_manifest.stage_completed(manifest, "patch-applied"):
                return problems

            had_pre_patch_blocker = PRE_PATCH_WORKTREE_BLOCKER in problems
            if not had_pre_patch_blocker:
                return problems
            problems = [
                problem for problem in problems if problem != PRE_PATCH_WORKTREE_BLOCKER
            ]
            if not _revision_source_is_unchanged(resolved, active):
                problems.append(
                    "revision implementation/worktree drift detected after revision start; "
                    "restore the revision-start source or start a new explicit revision"
                )
            return problems

        resume_problems._autodev_revision = True  # type: ignore[attr-defined]
        opencode_resume_status._resume_problems = resume_problems

    current_status = opencode_resume_status.status_text
    if not getattr(current_status, "_autodev_revision", False):
        original_status = current_status

        def status_text(repo: Path, mappings, **kwargs) -> str:
            resolved = Path(repo).expanduser().resolve()
            text = original_status(resolved, mappings, **kwargs).rstrip("\n")
            extra = _revision_status_text(resolved).rstrip("\n")
            return text + ("\n" + extra if extra else "") + "\n"

        status_text._autodev_revision = True  # type: ignore[attr-defined]
        opencode_resume_status.status_text = status_text

    current_coordinate = role_coordinator_flow.coordinate
    if not getattr(current_coordinate, "_autodev_revision", False):
        original_coordinate = current_coordinate

        def coordinate(repo: Path, **kwargs):
            resolved = Path(repo).expanduser().resolve()
            payload = original_coordinate(resolved, **kwargs)
            if payload.get("state") == "PR_READY":
                revision.mark_complete(resolved)
            return payload

        coordinate._autodev_revision = True  # type: ignore[attr-defined]
        role_coordinator_flow.coordinate = coordinate
