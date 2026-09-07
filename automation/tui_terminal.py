from __future__ import annotations

import os
import select
import shutil
import sys
import time
from dataclasses import dataclass
from typing import Callable, TextIO

from automation import tui_actions, tui_model


@dataclass
class ViewState:
    selected_index: int = 0
    detail: bool = False
    message: str = ""
    pending_action: str = ""
    quit: bool = False


def _clip(value: str, width: int) -> str:
    if width <= 0:
        return ""
    text = value.replace("\t", " ")
    if len(text) <= width:
        return text
    if width <= 3:
        return text[:width]
    return text[: width - 3] + "..."


def _bar(title: str, width: int) -> str:
    label = f" {title} "
    if len(label) >= width:
        return _clip(label, width)
    return label + "-" * (width - len(label))


def _queue_line(snapshot: tui_model.TuiSnapshot) -> str:
    q = snapshot.queue
    return (
        f"Queue managed={q.get('managed', 0)} ready={q.get('ready', 0)} "
        f"blocked={q.get('dependency_blocked', 0)} attention={q.get('attention_required', 0)} "
        f"running={q.get('running', 0)}"
    )


def _run_lines(snapshot: tui_model.TuiSnapshot) -> list[str]:
    run = snapshot.run
    issue = f"#{run.issue_number} {run.issue_title}".strip() if run.issue_number else "none"
    lines = [
        f"Run: {run.state} | issue={issue}",
        f"Status: {run.status or '-'} | next={run.next_stage or '-'} | action={run.next_action or '-'}",
        f"Branch: {run.branch or '-'}",
        f"PR: {run.pr_url or '-'}",
        f"Verification: local={'yes' if run.local_check_passed else 'no'} "
        f"semantic={'yes' if run.semantic_verified else 'no'}",
    ]
    if run.non_success_summary:
        lines.append(f"Non-success: {run.non_success_summary}")
    return lines


def _scheduler_lines(snapshot: tui_model.TuiSnapshot) -> list[str]:
    value = snapshot.scheduler
    fingerprint = value.runtime_fingerprint[:12] if value.runtime_fingerprint else "-"
    return [
        f"Scheduler: {value.state} | backend={value.backend or '-'}:{value.backend_state or '-'} "
        f"cadence={value.cadence_minutes or '-'}m",
        f"Runtime: {value.role_runtime or '-'} | fp={fingerprint} | notifications={value.notifications}",
        f"Last tick: {value.last_run_state or '-'} {value.last_run_at or ''}".rstrip(),
    ]


def _privacy_line(snapshot: tui_model.TuiSnapshot) -> str:
    value = snapshot.privacy
    return (
        f"Privacy enabled={'yes' if value.enabled else 'no'} local-only={'yes' if value.local_only else 'no'} "
        f"consent={value.consent_mode or '-'} grants={value.active_grants} active/"
        f"{value.expired_grants} expired/{value.revoked_grants} revoked"
    )


def _issue_lines(snapshot: tui_model.TuiSnapshot, state: ViewState, width: int, limit: int) -> list[str]:
    issues = snapshot.issues
    if not issues:
        return ["No managed/open queue entries loaded. Press r to refresh GitHub state."]
    state.selected_index = max(0, min(state.selected_index, len(issues) - 1))
    start = max(0, state.selected_index - max(0, limit // 2))
    end = min(len(issues), start + limit)
    start = max(0, end - limit)
    rows: list[str] = []
    for index in range(start, end):
        issue = issues[index]
        marker = ">" if index == state.selected_index else " "
        blockers = f" | blocked by {', '.join(issue.blockers)}" if issue.blockers else ""
        rows.append(
            _clip(
                f"{marker} #{issue.number:<5} {issue.queue_state:<10} {issue.title}{blockers}",
                width,
            )
        )
    return rows


def render(
    snapshot: tui_model.TuiSnapshot,
    state: ViewState,
    *,
    width: int = 100,
    height: int = 32,
) -> str:
    width = max(48, width)
    height = max(16, height)
    lines = [
        _bar("AutoDev TUI", width),
        _clip(f"Repository: {snapshot.repository} | {snapshot.repo_path}", width),
        _clip(_queue_line(snapshot), width),
        _clip(f"Active claims/runs: {snapshot.active_claims}", width),
    ]
    lines.extend(_clip(line, width) for line in _run_lines(snapshot))
    lines.extend(_clip(line, width) for line in _scheduler_lines(snapshot))
    lines.append(_clip(_privacy_line(snapshot), width))
    if snapshot.remote_error:
        lines.append(_clip(f"Remote refresh error: {snapshot.remote_error}", width))
    lines.append(_bar("Issues", width))

    reserved = 5
    issue_space = max(3, height - len(lines) - reserved)
    lines.extend(_issue_lines(snapshot, state, width, issue_space))

    selected = None
    if snapshot.issues and 0 <= state.selected_index < len(snapshot.issues):
        selected = snapshot.issues[state.selected_index]
    if state.detail and selected is not None:
        lines.append(_bar(f"Issue #{selected.number} detail", width))
        lines.append(_clip(selected.title, width))
        lines.append(_clip(f"State: {selected.queue_state} | URL: {selected.url}", width))
        if selected.blockers:
            lines.append(_clip("Blockers: " + "; ".join(selected.blockers), width))

    lines.append(_bar("Keys", width))
    if state.pending_action:
        action = tui_actions.ACTIONS[state.pending_action]
        lines.append(_clip(f"CONFIRM: {action.confirmation} [y/N]", width))
    else:
        lines.append(
            _clip(
                "Up/Down select  Enter detail  r refresh  m manage  c reconcile  n notifications  o open PR  q quit",
                width,
            )
        )
    if state.message:
        lines.append(_clip(state.message, width))
    return "\n".join(lines[:height])


def _selected_issue(snapshot: tui_model.TuiSnapshot, state: ViewState) -> int:
    if not snapshot.issues:
        return 0
    state.selected_index = max(0, min(state.selected_index, len(snapshot.issues) - 1))
    return snapshot.issues[state.selected_index].number


def apply_key(
    key: str,
    snapshot: tui_model.TuiSnapshot,
    state: ViewState,
    *,
    execute_action: Callable[..., str] = tui_actions.execute,
) -> tuple[ViewState, bool]:
    remote_refresh = False
    if state.pending_action:
        action = state.pending_action
        state.pending_action = ""
        if key.casefold() != "y":
            state.message = "Action cancelled."
            return state, False
        try:
            state.message = execute_action(
                action,
                snapshot,
                issue_number=_selected_issue(snapshot, state),
            )
            remote_refresh = action in {"manage", "reconcile"}
        except Exception as exc:
            state.message = f"Action failed: {exc}"
        return state, remote_refresh

    if key in {"q", "Q"}:
        state.quit = True
    elif key in {"up", "k"}:
        state.selected_index = max(0, state.selected_index - 1)
    elif key in {"down", "j"}:
        state.selected_index = min(max(0, len(snapshot.issues) - 1), state.selected_index + 1)
    elif key in {"enter", "\r", "\n"}:
        state.detail = not state.detail
    elif key in {"r", "R"}:
        remote_refresh = True
        state.message = "Refreshing repository state..."
    elif key == "m":
        if _selected_issue(snapshot, state):
            state.pending_action = "manage"
        else:
            state.message = "No issue selected."
    elif key == "c":
        state.pending_action = "reconcile"
    elif key == "n":
        state.pending_action = "notifications"
    elif key == "o":
        try:
            state.message = execute_action("open-pr", snapshot)
        except Exception as exc:
            state.message = f"Action failed: {exc}"
    return state, remote_refresh


class TerminalInput:
    def __init__(self, stream: TextIO = sys.stdin) -> None:
        self.stream = stream
        self._fd: int | None = None
        self._saved = None

    def __enter__(self) -> "TerminalInput":
        if os.name != "nt":
            import termios
            import tty

            self._fd = self.stream.fileno()
            self._saved = termios.tcgetattr(self._fd)
            tty.setcbreak(self._fd)
        return self

    def __exit__(self, *_args) -> None:
        if os.name != "nt" and self._fd is not None and self._saved is not None:
            import termios

            termios.tcsetattr(self._fd, termios.TCSADRAIN, self._saved)

    def read(self, timeout: float) -> str:
        if os.name == "nt":
            import msvcrt

            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if msvcrt.kbhit():
                    value = msvcrt.getwch()
                    if value in {"\x00", "\xe0"}:
                        code = msvcrt.getwch()
                        return {"H": "up", "P": "down"}.get(code, "")
                    if value == "\r":
                        return "enter"
                    return value
                time.sleep(0.02)
            return ""

        assert self._fd is not None
        readable, _, _ = select.select([self._fd], [], [], timeout)
        if not readable:
            return ""
        first = os.read(self._fd, 1).decode("utf-8", errors="ignore")
        if first == "\x1b":
            readable, _, _ = select.select([self._fd], [], [], 0.01)
            if readable:
                rest = os.read(self._fd, 2).decode("utf-8", errors="ignore")
                return {"[A": "up", "[B": "down"}.get(rest, "")
        if first in {"\r", "\n"}:
            return "enter"
        return first


def run_interactive(
    snapshot: tui_model.TuiSnapshot,
    *,
    remote_refresh_seconds: float = 30.0,
    local_refresh_seconds: float = 2.0,
    stdout: TextIO = sys.stdout,
    refresh_local: Callable[[tui_model.TuiSnapshot], tui_model.TuiSnapshot] = tui_model.refresh_local,
    refresh_remote: Callable[[tui_model.TuiSnapshot], tui_model.TuiSnapshot] = tui_model.collect_remote,
) -> int:
    if not getattr(sys.stdin, "isatty", lambda: False)() or not getattr(stdout, "isatty", lambda: False)():
        raise RuntimeError("interactive TUI requires a controlling terminal; use `autodev tui --once` for a snapshot")

    state = ViewState()
    last_local = 0.0
    last_remote = time.monotonic()
    stdout.write("\x1b[?1049h\x1b[?25l")
    stdout.flush()
    try:
        with TerminalInput(sys.stdin) as reader:
            while not state.quit:
                now = time.monotonic()
                if now - last_local >= local_refresh_seconds:
                    snapshot = refresh_local(snapshot)
                    last_local = now
                if now - last_remote >= remote_refresh_seconds:
                    snapshot = refresh_remote(snapshot)
                    last_remote = now
                    state.message = "Repository state refreshed."
                size = shutil.get_terminal_size((100, 32))
                frame = render(snapshot, state, width=size.columns, height=size.lines)
                stdout.write("\x1b[H\x1b[2J" + frame)
                stdout.flush()
                pressed = reader.read(0.25)
                if pressed:
                    state, force_remote = apply_key(pressed, snapshot, state)
                    if force_remote:
                        snapshot = refresh_remote(snapshot)
                        last_remote = time.monotonic()
                        if state.message.startswith("Refreshing"):
                            state.message = "Repository state refreshed."
    finally:
        stdout.write("\x1b[?25h\x1b[?1049l")
        stdout.flush()
    return 0
