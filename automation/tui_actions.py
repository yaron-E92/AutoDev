from __future__ import annotations

import io
import subprocess
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from automation import (
    manage_cli,
    queue_workflow,
    scheduler,
    scheduler_health_contract,
    scheduler_health_storage,
    tui_model,
)


@dataclass(frozen=True)
class TuiAction:
    name: str
    label: str
    confirmation: str = ""

    @property
    def mutating(self) -> bool:
        return bool(self.confirmation)


ACTIONS = {
    "manage": TuiAction(
        "manage",
        "manage selected issue",
        "Add autodev:managed to the selected open issue?",
    ),
    "reconcile": TuiAction(
        "reconcile",
        "reconcile queue",
        "Reconcile derived queue labels for this repository?",
    ),
    "notifications": TuiAction(
        "notifications",
        "toggle scheduler notifications",
        "Change scheduler notification state?",
    ),
    "open-pr": TuiAction("open-pr", "open current PR"),
}


class TuiActionError(RuntimeError):
    pass


def execute(
    action: str,
    snapshot: tui_model.TuiSnapshot,
    *,
    issue_number: int = 0,
    runner: Callable[..., object] = subprocess.run,
    opener: Callable[[str], object] = webbrowser.open,
) -> str:
    if action == "manage":
        number = issue_number or snapshot.selected_issue_number
        if number <= 0:
            raise TuiActionError("no issue is selected")
        output = io.StringIO()
        error = io.StringIO()
        code = manage_cli.run_cli(
            [str(number), "--repo", snapshot.repo_path, "--github-repo", snapshot.repository],
            runner=runner,
            stdout=output,
            stderr=error,
        )
        if code != 0:
            raise TuiActionError(error.getvalue().strip() or f"manage exited {code}")
        return output.getvalue().strip() or f"managed issue #{number}"

    if action == "reconcile":
        try:
            states = queue_workflow.reconcile_queue(
                Path(snapshot.repo_path), snapshot.repository, runner=runner
            )
        except Exception as exc:
            raise TuiActionError(str(exc)) from exc
        changed = sum(1 for item in states if item.changed)
        return f"queue reconciled; {changed} issue(s) changed"

    if action == "notifications":
        try:
            registration = scheduler.registration_path(snapshot.repository)
            loaded = scheduler._load_registration(registration)  # type: ignore[attr-defined]
            if loaded is None:
                raise TuiActionError("scheduler is not installed")
            current = scheduler_health_storage.load_notification_policy(registration)
            if current.backend == scheduler_health_contract.NOTIFICATION_OFF:
                replacement = scheduler_health_contract.NotificationPolicy(
                    backend=scheduler_health_contract.NOTIFICATION_NATIVE
                )
                result = "enabled"
            else:
                replacement = scheduler_health_contract.NotificationPolicy()
                result = "disabled"
            scheduler_health_storage.save_notification_policy(registration, replacement)
            return f"scheduler notifications {result}"
        except TuiActionError:
            raise
        except Exception as exc:
            raise TuiActionError(str(exc)) from exc

    if action == "open-pr":
        if not snapshot.run.pr_url:
            raise TuiActionError("current run has no PR URL")
        try:
            opener(snapshot.run.pr_url)
        except Exception as exc:
            raise TuiActionError(f"could not open PR URL: {exc}") from exc
        return snapshot.run.pr_url

    raise TuiActionError(f"unknown TUI action: {action}")
