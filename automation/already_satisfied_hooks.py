from __future__ import annotations

import subprocess
from pathlib import Path

from automation import (
    already_satisfied,
    already_satisfied_labels,
    non_success_report,
    opencode_github_entrypoint,
    opencode_resume_status,
    queue_selection,
    role_coordinator_flow,
    role_coordinator_runtime,
    scheduler_types,
    workflow_stages,
)


class AlreadySatisfiedComplete(RuntimeError):
    """Internal control signal after evidence-backed no-op confirmation."""


def _append_prompt_context(path: Path, context: str) -> None:
    if not context:
        return
    try:
        existing = path.read_text(encoding="utf-8")
        path.write_text(existing.rstrip() + context + "\n", encoding="utf-8")
    except OSError as exc:
        raise already_satisfied.AlreadySatisfiedError(
            f"cannot bind already-satisfied verification context to {path}: {exc}"
        ) from exc


def _already_satisfied_status_text(repo: Path) -> str:
    if not already_satisfied.current_is_confirmed(repo):
        return ""
    try:
        state = workflow_stages.read_state(repo / workflow_stages.CURRENT_DIR)
    except workflow_stages.WorkflowStageError:
        return ""
    return (
        "Outcome: already satisfied on prepared base\n"
        f"Prepared base: {state.get('AlreadySatisfiedBaseSha', state.get('BaseSha', ''))}\n"
        "Repository modified: no\n"
        "Commit: none\n"
        "PR: none\n"
    )


def _normalized(value: str) -> str:
    return str(value or "").casefold().replace("_", "").replace("-", "")


def _run_role_with_probe(
    original_run_role,
    repo: Path,
    role: str,
    runtime,
    snapshots: dict[str, object],
    *,
    repair_kind: str = "",
    already_prepared: bool = False,
    runner=subprocess.run,
    which=None,
):
    original_repo = Path(repo)
    resolved = original_repo.expanduser().resolve()
    if role == "implementer" and already_satisfied.candidate_in_plan(resolved):
        def verify_candidate():
            result = original_run_role(
                original_repo,
                "verifier",
                runtime,
                snapshots,
                runner=runner,
                which=which,
            )
            state = workflow_stages.read_state(
                resolved / workflow_stages.CURRENT_DIR
            )
            already_satisfied_labels.ensure_done_label(
                resolved,
                str(state.get("RepoFullName", "")),
                runner=runner,
            )
            return result

        result = already_satisfied.probe_candidate(
            resolved,
            semantic_verifier=verify_candidate,
            runner=runner,
            which=which,
        )
        if result == "confirmed":
            raise AlreadySatisfiedComplete()
    return original_run_role(
        original_repo,
        role,
        runtime,
        snapshots,
        repair_kind=repair_kind,
        already_prepared=already_prepared,
        runner=runner,
        which=which,
    )


def install() -> None:
    if getattr(install, "_autodev_already_satisfied", False):
        return

    # scheduler imports opencode_entrypoint, which installs this module. Keep the
    # scheduler dependency lazy so the top-level production import graph remains acyclic.
    from automation import scheduler

    current_prepare = role_coordinator_runtime._prepare_role
    if not getattr(current_prepare, "_autodev_already_satisfied", False):
        original_prepare = current_prepare

        def prepare_role(repo: Path, role: str, *, repair_kind: str = "") -> None:
            resolved = Path(repo).expanduser().resolve()
            original_prepare(resolved, role, repair_kind=repair_kind)
            if role != "verifier":
                return
            prompt = resolved / workflow_stages.CURRENT_DIR / "verifier.md"
            _append_prompt_context(
                prompt,
                already_satisfied.role_prompt_context(resolved, role),
            )

        prepare_role._autodev_already_satisfied = True  # type: ignore[attr-defined]
        role_coordinator_runtime._prepare_role = prepare_role

    current_run_role = role_coordinator_runtime.run_role
    if not getattr(current_run_role, "_autodev_already_satisfied", False):
        original_run_role = current_run_role

        def run_role(
            repo: Path,
            role: str,
            runtime,
            snapshots: dict[str, object],
            *,
            repair_kind: str = "",
            already_prepared: bool = False,
            runner=subprocess.run,
            which=None,
        ):
            return _run_role_with_probe(
                original_run_role,
                repo,
                role,
                runtime,
                snapshots,
                repair_kind=repair_kind,
                already_prepared=already_prepared,
                runner=runner,
                which=which,
            )

        run_role._autodev_already_satisfied = True  # type: ignore[attr-defined]
        role_coordinator_runtime.run_role = run_role
        # role_coordinator_flow imports run_role by name, so update that alias too.
        role_coordinator_flow.run_role = run_role

    current_action = opencode_resume_status.resume_action
    if not getattr(current_action, "_autodev_already_satisfied", False):
        original_action = current_action

        def resume_action(manifest: dict[str, object], state: dict[str, object]) -> str:
            if already_satisfied.record_is_confirmed(manifest, state):
                return "already-satisfied"
            return original_action(manifest, state)

        resume_action._autodev_already_satisfied = True  # type: ignore[attr-defined]
        opencode_resume_status.resume_action = resume_action

    current_status = opencode_resume_status.status_text
    if not getattr(current_status, "_autodev_already_satisfied", False):
        original_status = current_status

        def status_text(repo: Path, mappings, **kwargs) -> str:
            resolved = Path(repo).expanduser().resolve()
            text = original_status(resolved, mappings, **kwargs).rstrip("\n")
            extra = _already_satisfied_status_text(resolved).rstrip("\n")
            return text + ("\n" + extra if extra else "") + "\n"

        status_text._autodev_already_satisfied = True  # type: ignore[attr-defined]
        opencode_resume_status.status_text = status_text

    current_coordinate = role_coordinator_flow.coordinate
    if not getattr(current_coordinate, "_autodev_already_satisfied", False):
        original_coordinate = current_coordinate

        def coordinate(repo: Path, **kwargs):
            resolved = Path(repo).expanduser().resolve()
            if already_satisfied.current_is_confirmed(resolved):
                return already_satisfied.terminal_payload(resolved)
            try:
                return original_coordinate(resolved, **kwargs)
            except AlreadySatisfiedComplete:
                return already_satisfied.terminal_payload(resolved)

        coordinate._autodev_already_satisfied = True  # type: ignore[attr-defined]
        role_coordinator_flow.coordinate = coordinate

    current_inspect = queue_selection.inspect_existing_run
    if not getattr(current_inspect, "_autodev_already_satisfied", False):
        original_inspect = current_inspect

        def inspect_existing_run(repo: Path):
            resolved = Path(repo).expanduser().resolve()
            current = resolved / workflow_stages.CURRENT_DIR
            if (current / "state.json").is_file():
                try:
                    state = workflow_stages.read_state(current)
                except workflow_stages.WorkflowStageError:
                    state = {}
                if _normalized(str(state.get("Status", ""))) == "alreadysatisfied":
                    return queue_selection.ExistingRun("NONE")
            return original_inspect(resolved)

        inspect_existing_run._autodev_already_satisfied = True  # type: ignore[attr-defined]
        queue_selection.inspect_existing_run = inspect_existing_run

    current_terminal = scheduler._claim_terminal_state
    if not getattr(current_terminal, "_autodev_already_satisfied", False):
        original_terminal = current_terminal

        def claim_terminal_state(coordinator_state: str) -> bool:
            if _normalized(coordinator_state) == "alreadysatisfied":
                return True
            return original_terminal(coordinator_state)

        claim_terminal_state._autodev_already_satisfied = True  # type: ignore[attr-defined]
        scheduler._claim_terminal_state = claim_terminal_state

    current_dispatch = scheduler_types._dispatch_state
    if not getattr(current_dispatch, "_autodev_already_satisfied", False):
        original_dispatch = current_dispatch

        def dispatch_state(coordinator_state: str, *, coordinator_exit_code: int = 0) -> str:
            if _normalized(coordinator_state) == "alreadysatisfied":
                return "ALREADY_SATISFIED"
            return original_dispatch(
                coordinator_state,
                coordinator_exit_code=coordinator_exit_code,
            )

        dispatch_state._autodev_already_satisfied = True  # type: ignore[attr-defined]
        scheduler_types._dispatch_state = dispatch_state
        # scheduler imports this helper by name.
        scheduler._dispatch_state = dispatch_state

    current_report = non_success_report.update_report
    if not getattr(current_report, "_autodev_already_satisfied", False):
        original_report = current_report

        def update_report(repo: Path, payload: dict[str, object]):
            resolved = Path(repo).expanduser().resolve()
            if str(payload.get("state", "")) == "ALREADY_SATISFIED":
                (
                    resolved
                    / workflow_stages.CURRENT_DIR
                    / non_success_report.REPORT_NAME
                ).unlink(missing_ok=True)
                (
                    resolved
                    / ".autodev-run"
                    / "last-operation"
                    / non_success_report.REPORT_NAME
                ).unlink(missing_ok=True)
                result = dict(payload)
                result.pop("non_success_report", None)
                result.pop("non_success_report_error", None)
                return result, ""
            return original_report(resolved, payload)

        update_report._autodev_already_satisfied = True  # type: ignore[attr-defined]
        non_success_report.update_report = update_report

    opencode_github_entrypoint.SUCCESSFUL_TERMINAL_STATES.add("ALREADY_SATISFIED")
    install._autodev_already_satisfied = True  # type: ignore[attr-defined]
