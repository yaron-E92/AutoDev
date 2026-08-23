from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, TextIO

from automation import issue_queue, privacy, privacy_grants, queue_selection, scheduler, workflow_stages


HEALTH_SCHEMA = 1
NOTIFICATION_SCHEMA = 1
HEALTH_FILE = "health.json"
NOTIFICATION_FILE = "notifications.json"
NOTIFICATION_OFF = "off"
NOTIFICATION_NATIVE = "native"
NOTIFICATION_BACKENDS = (NOTIFICATION_OFF, NOTIFICATION_NATIVE)
REMINDER_STATES = {"ATTENTION_REQUIRED", "SCHEDULER_ERROR"}
HEALTH_STATES = {
    "READY_WORK_AVAILABLE",
    "RUNNING_OR_RESUMABLE",
    "NO_READY_WORK",
    "ALL_MANAGED_WORK_BLOCKED",
    "ATTENTION_REQUIRED",
    "PR_READY",
    "SCHEDULER_ERROR",
}


class SchedulerHealthError(RuntimeError):
    pass


@dataclass(frozen=True)
class NotificationPolicy:
    backend: str = NOTIFICATION_OFF
    reminder_hours: int = 0

    @property
    def enabled(self) -> bool:
        return self.backend != NOTIFICATION_OFF

    def to_json(self) -> dict[str, object]:
        return {
            "schema_version": NOTIFICATION_SCHEMA,
            "backend": self.backend,
            "reminder_hours": self.reminder_hours,
        }


@dataclass(frozen=True)
class HealthSnapshot:
    state: str
    repository: str
    observed_at: str
    fingerprint: str
    queue: dict[str, int]
    unmanaged_open: int
    issue_number: int = 0
    run_state: str = ""
    next_stage: str = ""
    next_action: str = ""
    last_outcome: str = ""
    attention_kind: str = ""
    privacy_grants: dict[str, int] | None = None
    blocker_counts: dict[str, int] | None = None

    def to_json(self) -> dict[str, object]:
        value = asdict(self)
        value["privacy_grants"] = dict(self.privacy_grants or {})
        value["blocker_counts"] = dict(self.blocker_counts or {})
        return value


@dataclass(frozen=True)
class NotificationResult:
    attempted: bool
    delivered: bool
    backend: str
    reason: str = ""

    def to_json(self) -> dict[str, object]:
        return asdict(self)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_time(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def health_path(registration_file: Path) -> Path:
    return registration_file.expanduser().resolve().parent / HEALTH_FILE


def notification_path(registration_file: Path) -> Path:
    return registration_file.expanduser().resolve().parent / NOTIFICATION_FILE


def _read_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_json(path: Path, value: dict[str, object]) -> None:
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    try:
        temp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temp.replace(path)
    except OSError as exc:
        temp.unlink(missing_ok=True)
        raise SchedulerHealthError(f"cannot write scheduler health state {path}: {exc}") from exc


def load_notification_policy(registration_file: Path) -> NotificationPolicy:
    raw = _read_json(notification_path(registration_file))
    if not raw:
        return NotificationPolicy()
    if raw.get("schema_version") != NOTIFICATION_SCHEMA:
        raise SchedulerHealthError("unsupported scheduler notification policy schema")
    backend = str(raw.get("backend", NOTIFICATION_OFF)).casefold()
    if backend not in NOTIFICATION_BACKENDS:
        raise SchedulerHealthError(f"unsupported scheduler notification backend: {backend}")
    reminder_hours = int(raw.get("reminder_hours", 0) or 0)
    if reminder_hours < 0 or reminder_hours > 24 * 365:
        raise SchedulerHealthError("notification reminder hours must be between 0 and 8760")
    return NotificationPolicy(backend=backend, reminder_hours=reminder_hours)


def save_notification_policy(registration_file: Path, policy: NotificationPolicy) -> None:
    if policy.backend not in NOTIFICATION_BACKENDS:
        raise SchedulerHealthError(f"unsupported scheduler notification backend: {policy.backend}")
    if policy.reminder_hours < 0 or policy.reminder_hours > 24 * 365:
        raise SchedulerHealthError("notification reminder hours must be between 0 and 8760")
    _write_json(notification_path(registration_file), policy.to_json())


def _privacy_grant_summary(repo: Path) -> dict[str, int]:
    counts = {"active": 0, "expired": 0, "revoked": 0}
    for record in privacy_grants.current_grants(repo):
        status = str(record.get("status", ""))
        if status in counts:
            counts[status] += 1
    return counts


def _privacy_probe(
    repo: Path,
    *,
    runner: Callable[..., object] = subprocess.run,
    which: Callable[[str], str | None] = shutil.which,
) -> tuple[bool, dict[str, int]]:
    counts = _privacy_grant_summary(repo)
    policy = privacy.load_policy(repo)
    if not policy.enabled or policy.local_only or policy.consent_mode != "explicit":
        return False, counts
    try:
        required = privacy_grants._resolve_requirements(repo, runner=runner, which=which)  # type: ignore[attr-defined]
    except Exception:
        # Health remains useful even when optional route introspection is unavailable.
        # The actual coordinator privacy gate still fails closed before any model call.
        return False, counts
    uncovered = [
        item
        for item in required
        if privacy_grants.matching_grant(repo, policy, item) is None
    ]
    return bool(uncovered), counts


def _raw_run_status(repo: Path) -> tuple[str, int]:
    current = repo.expanduser().resolve() / workflow_stages.CURRENT_DIR
    if not current.is_dir():
        return "", 0
    try:
        state = workflow_stages.read_state(current)
    except Exception:
        return "", 0
    return (
        str(state.get("Status", "")),
        int(state.get("IssueNumber", 0) or 0),
    )


def _blocker_counts(states: list[issue_queue.QueueState]) -> dict[str, int]:
    counts: dict[int, int] = {}
    for state in states:
        if state.reason != "blocked":
            continue
        for blocker in state.open_blockers:
            counts[blocker.number] = counts.get(blocker.number, 0) + 1
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return {str(number): count for number, count in ordered}


def _first_issue_number(states: list[issue_queue.QueueState], reason: str) -> int:
    return min(
        (state.issue.number for state in states if state.reason == reason),
        default=0,
    )


def _fingerprint_source(
    *,
    state: str,
    repository: str,
    queue: dict[str, int],
    unmanaged_open: int,
    issue_number: int,
    run_state: str,
    next_stage: str,
    next_action: str,
    last_outcome: str,
    attention_kind: str,
    privacy_grants: dict[str, int],
    blocker_counts: dict[str, int],
) -> dict[str, object]:
    return {
        "state": state,
        "repository": repository,
        "queue": queue,
        "unmanaged_open": unmanaged_open,
        "issue_number": issue_number,
        "run_state": run_state,
        "next_stage": next_stage,
        "next_action": next_action,
        "last_outcome": last_outcome,
        "attention_kind": attention_kind,
        "privacy_grants": privacy_grants,
        "blocker_counts": blocker_counts,
    }


def _fingerprint(value: dict[str, object]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def compute_health(
    repo: Path,
    github_repo: str,
    *,
    runner: Callable[..., object] = subprocess.run,
    which: Callable[[str], str | None] = shutil.which,
    now: datetime | None = None,
    force_error: bool = False,
    last_outcome: str = "",
    privacy_probe: Callable[[Path], tuple[bool, dict[str, int]]] | None = None,
) -> HealthSnapshot:
    repo = repo.expanduser().resolve()
    states = issue_queue.inspect_queue(repo, github_repo, runner=runner)
    summary = issue_queue.queue_summary(states)
    issues = issue_queue.list_issues(repo, github_repo, runner=runner)
    unmanaged_open = sum(
        issue.state == "open" and issue_queue.MANAGED_LABEL not in issue.labels
        for issue in issues
    )
    existing = queue_selection.inspect_existing_run(repo)
    raw_status, raw_issue_number = _raw_run_status(repo)
    probe = privacy_probe or (lambda path: _privacy_probe(path, runner=runner, which=which))
    privacy_blocked, grant_counts = probe(repo)
    blockers = _blocker_counts(states)

    issue_number = existing.issue_number or raw_issue_number
    attention_kind = ""
    normalized_raw = raw_status.casefold().replace("_", "").replace("-", "")
    normalized_outcome = last_outcome.casefold().replace("_", "").replace("-", "")

    if privacy_blocked and (summary["ready"] > 0 or existing.state == "RESUME_EXISTING"):
        state = "ATTENTION_REQUIRED"
        attention_kind = "privacy-consent"
    elif normalized_raw in {"readyforreview", "prready"} or normalized_outcome in {
        "readyforreview",
        "prready",
    }:
        state = "PR_READY"
    elif existing.state == "ATTENTION_REQUIRED" or summary["attention_required"] > 0:
        state = "ATTENTION_REQUIRED"
        attention_kind = "manual-or-queue-attention"
    elif force_error or existing.state == "RUN_HEALTH_BLOCKED":
        state = "SCHEDULER_ERROR"
    elif existing.state == "RESUME_EXISTING" or summary["running"] > 0:
        state = "RUNNING_OR_RESUMABLE"
    elif summary["ready"] > 0:
        state = "READY_WORK_AVAILABLE"
    elif summary["managed"] > 0 and summary["dependency_blocked"] == summary["managed"]:
        state = "ALL_MANAGED_WORK_BLOCKED"
    elif summary["policy_excluded"] > 0:
        state = "ATTENTION_REQUIRED"
        attention_kind = "repository-policy"
    else:
        state = "NO_READY_WORK"

    if not issue_number:
        if state == "ATTENTION_REQUIRED" and attention_kind == "privacy-consent":
            issue_number = _first_issue_number(states, "ready")
        elif state == "ATTENTION_REQUIRED":
            issue_number = _first_issue_number(states, "attention")
        elif state == "RUNNING_OR_RESUMABLE":
            issue_number = _first_issue_number(states, "running")

    source = _fingerprint_source(
        state=state,
        repository=github_repo,
        queue=summary,
        unmanaged_open=unmanaged_open,
        issue_number=issue_number,
        run_state=existing.state,
        next_stage=existing.next_stage,
        next_action=existing.next_action,
        last_outcome=last_outcome,
        attention_kind=attention_kind,
        privacy_grants=grant_counts,
        blocker_counts=blockers,
    )
    return HealthSnapshot(
        state=state,
        repository=github_repo,
        observed_at=_iso(now or _now()),
        fingerprint=_fingerprint(source),
        queue=summary,
        unmanaged_open=unmanaged_open,
        issue_number=issue_number,
        run_state=existing.state,
        next_stage=existing.next_stage,
        next_action=existing.next_action,
        last_outcome=last_outcome,
        attention_kind=attention_kind,
        privacy_grants=grant_counts,
        blocker_counts=blockers,
    )


def render_health(snapshot: HealthSnapshot) -> str:
    queue = snapshot.queue
    prefix = (
        f"{queue.get('ready', 0)} ready, "
        f"{queue.get('dependency_blocked', 0)} dependency-blocked, "
        f"{snapshot.unmanaged_open} unmanaged open issue(s)"
    )
    if snapshot.state == "READY_WORK_AVAILABLE":
        return f"READY_WORK_AVAILABLE: {prefix}."
    if snapshot.state == "RUNNING_OR_RESUMABLE":
        stage = snapshot.next_stage or "durable checkpoint"
        issue = f"Issue #{snapshot.issue_number} " if snapshot.issue_number else "AutoDev run "
        return f"RUNNING_OR_RESUMABLE: {issue}is safely resumable from {stage}."
    if snapshot.state == "NO_READY_WORK":
        return f"NO_READY_WORK: {prefix}."
    if snapshot.state == "ALL_MANAGED_WORK_BLOCKED":
        top = next(iter((snapshot.blocker_counts or {}).items()), None)
        suffix = f" Top blocker #{top[0]} blocks {top[1]} managed issue(s)." if top else ""
        return (
            f"ALL_MANAGED_WORK_BLOCKED: all {queue.get('managed', 0)} managed open issue(s) "
            f"are dependency-blocked.{suffix}"
        )
    if snapshot.state == "ATTENTION_REQUIRED":
        issue = f"Issue #{snapshot.issue_number} " if snapshot.issue_number else "AutoDev "
        if snapshot.attention_kind == "privacy-consent":
            return (
                f"ATTENTION_REQUIRED: {issue}requires privacy consent before autonomous model work; "
                "the privacy gate prevents model content from being sent without authorization."
            )
        return f"ATTENTION_REQUIRED: {issue}requires developer attention before autonomous work can continue."
    if snapshot.state == "PR_READY":
        issue = f"Issue #{snapshot.issue_number} " if snapshot.issue_number else "AutoDev work "
        return f"PR_READY: {issue}is ready for review/merge."
    return "SCHEDULER_ERROR: the autonomous scheduler or durable run requires inspection."


def _notification_message(snapshot: HealthSnapshot) -> tuple[str, str]:
    title = f"AutoDev · {snapshot.repository}"
    # render_health is deliberately bounded to deterministic metadata only.
    return title, render_health(snapshot)


def _native_notify(
    title: str,
    message: str,
    *,
    runner: Callable[..., object] = subprocess.run,
    which: Callable[[str], str | None] = shutil.which,
    platform_name: str | None = None,
) -> NotificationResult:
    platform = (platform_name or ("windows" if os.name == "nt" else "posix")).casefold()
    if platform == "windows":
        executable = which("msg") or which("msg.exe")
        if not executable:
            return NotificationResult(True, False, NOTIFICATION_NATIVE, "msg.exe is unavailable")
        argv = [executable, "*", "/TIME:10", f"{title}: {message}"]
    else:
        executable = which("notify-send")
        if not executable:
            return NotificationResult(True, False, NOTIFICATION_NATIVE, "notify-send is unavailable")
        argv = [executable, title, message]
    try:
        completed = runner(
            argv,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
        )
    except OSError:
        return NotificationResult(True, False, NOTIFICATION_NATIVE, "native notifier could not be launched")
    if int(getattr(completed, "returncode", 1)) != 0:
        return NotificationResult(True, False, NOTIFICATION_NATIVE, "native notifier returned a nonzero exit code")
    return NotificationResult(True, True, NOTIFICATION_NATIVE)


def _should_notify(
    previous: HealthSnapshot | None,
    current: HealthSnapshot,
    record: dict[str, object],
    policy: NotificationPolicy,
    *,
    now: datetime,
) -> tuple[bool, str]:
    if not policy.enabled:
        return False, "notifications-disabled"
    if previous is None:
        if current.state in {"ATTENTION_REQUIRED", "SCHEDULER_ERROR", "ALL_MANAGED_WORK_BLOCKED", "PR_READY"}:
            return True, "initial-actionable-state"
        return False, "initial-benign-state"
    if previous.fingerprint != current.fingerprint:
        return True, "material-transition"
    if current.state not in REMINDER_STATES or policy.reminder_hours <= 0:
        return False, "unchanged-state"
    last_notification = record.get("last_notification", {})
    last_notification = last_notification if isinstance(last_notification, dict) else {}
    last_at = _parse_time(last_notification.get("at"))
    if last_at is None or now - last_at >= timedelta(hours=policy.reminder_hours):
        return True, "attention-reminder-cooldown"
    return False, "cooldown-active"


def _snapshot_from_json(raw: object) -> HealthSnapshot | None:
    if not isinstance(raw, dict):
        return None
    state = str(raw.get("state", ""))
    if state not in HEALTH_STATES:
        return None
    queue = raw.get("queue", {})
    privacy_counts = raw.get("privacy_grants", {})
    blocker_counts = raw.get("blocker_counts", {})
    if not isinstance(queue, dict) or not isinstance(privacy_counts, dict) or not isinstance(blocker_counts, dict):
        return None
    return HealthSnapshot(
        state=state,
        repository=str(raw.get("repository", "")),
        observed_at=str(raw.get("observed_at", "")),
        fingerprint=str(raw.get("fingerprint", "")),
        queue={str(k): int(v) for k, v in queue.items()},
        unmanaged_open=int(raw.get("unmanaged_open", 0) or 0),
        issue_number=int(raw.get("issue_number", 0) or 0),
        run_state=str(raw.get("run_state", "")),
        next_stage=str(raw.get("next_stage", "")),
        next_action=str(raw.get("next_action", "")),
        last_outcome=str(raw.get("last_outcome", "")),
        attention_kind=str(raw.get("attention_kind", "")),
        privacy_grants={str(k): int(v) for k, v in privacy_counts.items()},
        blocker_counts={str(k): int(v) for k, v in blocker_counts.items()},
    )


def observe_health(
    registration_file: Path,
    snapshot: HealthSnapshot,
    *,
    policy: NotificationPolicy | None = None,
    notifier: Callable[[str, str], NotificationResult] | None = None,
    now: datetime | None = None,
) -> NotificationResult:
    path = health_path(registration_file)
    record = _read_json(path)
    if record and record.get("schema_version") != HEALTH_SCHEMA:
        raise SchedulerHealthError("unsupported scheduler health state schema")
    previous = _snapshot_from_json(record.get("current"))
    current_time = (now or _now()).astimezone(timezone.utc)
    notification_policy = policy or load_notification_policy(registration_file)
    should_notify, reason = _should_notify(previous, snapshot, record, notification_policy, now=current_time)
    notification = NotificationResult(False, False, notification_policy.backend, reason)
    if should_notify:
        title, message = _notification_message(snapshot)
        if notifier is not None:
            try:
                notification = notifier(title, message)
            except Exception:
                notification = NotificationResult(True, False, notification_policy.backend, "notification delivery raised an exception")
        elif notification_policy.backend == NOTIFICATION_NATIVE:
            notification = _native_notify(title, message)
        else:
            notification = NotificationResult(False, False, notification_policy.backend, "notifications-disabled")
        notification_record = {
            "at": _iso(current_time),
            "fingerprint": snapshot.fingerprint,
            "state": snapshot.state,
            "backend": notification.backend,
            "delivered": notification.delivered,
            "reason": reason,
        }
    else:
        prior = record.get("last_notification")
        notification_record = dict(prior) if isinstance(prior, dict) else {}

    previous_state = previous.state if previous else ""
    transition = record.get("last_transition")
    transition_record = dict(transition) if isinstance(transition, dict) else {}
    if previous is None or previous.fingerprint != snapshot.fingerprint:
        transition_record = {
            "at": snapshot.observed_at,
            "from": previous_state,
            "to": snapshot.state,
            "fingerprint": snapshot.fingerprint,
        }

    payload = {
        "schema_version": HEALTH_SCHEMA,
        "current": snapshot.to_json(),
        "last_transition": transition_record,
        "last_notification": notification_record,
    }
    _write_json(path, payload)
    return notification


def _location_parser(prog: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=prog, add_help=False)
    parser.add_argument("--repo", default=".")
    parser.add_argument("--github-repo", default="")
    parser.add_argument("--registration", default="")
    parser.add_argument("--json", action="store_true")
    return parser


def _resolve_registration(
    *,
    repo: Path,
    github_repo: str,
    registration: str,
    home: Path | None,
    runner: Callable[..., object],
) -> tuple[Path, scheduler.SchedulerRegistration]:
    if registration:
        path = Path(registration).expanduser().resolve()
    else:
        source = scheduler._repo_root(repo)  # type: ignore[attr-defined]
        resolved = issue_queue.resolve_github_repo(source, explicit=github_repo, runner=runner)
        path = scheduler.registration_path(resolved, home=home)
    loaded = scheduler._load_registration(path)  # type: ignore[attr-defined]
    if loaded is None:
        raise SchedulerHealthError(f"scheduler is not installed: {path}")
    return path, loaded


def current_health(
    registration_file: Path,
    registration: scheduler.SchedulerRegistration,
    *,
    runner: Callable[..., object] = subprocess.run,
    which: Callable[[str], str | None] = shutil.which,
    force_error: bool = False,
) -> HealthSnapshot:
    worker = Path(registration.worker_repository).expanduser().resolve()
    if not worker.is_dir() or not (worker / ".git").exists():
        now = _now()
        source = _fingerprint_source(
            state="SCHEDULER_ERROR",
            repository=registration.github_repository,
            queue={},
            unmanaged_open=0,
            issue_number=0,
            run_state="",
            next_stage="",
            next_action="",
            last_outcome="",
            attention_kind="",
            privacy_grants={},
            blocker_counts={},
        )
        return HealthSnapshot(
            state="SCHEDULER_ERROR",
            repository=registration.github_repository,
            observed_at=_iso(now),
            fingerprint=_fingerprint(source),
            queue={},
            unmanaged_open=0,
        )
    latest = scheduler._load_registration(registration_file) or registration  # type: ignore[attr-defined]
    last_run = latest.last_run or {}
    last_outcome = str(last_run.get("state", ""))
    return compute_health(
        worker,
        registration.github_repository,
        runner=runner,
        which=which,
        force_error=force_error,
        last_outcome=last_outcome,
    )


def run_tick(
    argv: list[str],
    *,
    home: Path | None = None,
    runner: Callable[..., object] = subprocess.run,
    which: Callable[[str], str | None] = shutil.which,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    args, _unknown = _location_parser("autodev scheduler run-once").parse_known_args(argv[1:] if argv and argv[0] == "run-once" else argv)
    registration_file: Path | None = None
    registration: scheduler.SchedulerRegistration | None = None
    try:
        registration_file, registration = _resolve_registration(
            repo=Path(args.repo),
            github_repo=args.github_repo,
            registration=args.registration,
            home=home,
            runner=runner,
        )
    except Exception:
        # Let the canonical scheduler surface installation/location errors.
        pass

    code = scheduler.run_cli(
        argv,
        home=home,
        runner=runner,
        which=which,
        stdout=stdout,
        stderr=stderr,
    )
    if registration_file is None or registration is None:
        return code
    try:
        snapshot = current_health(
            registration_file,
            registration,
            runner=runner,
            which=which,
            force_error=code != 0,
        )
        observe_health(registration_file, snapshot)
    except Exception:
        # Health/notification reporting must never replace the scheduler's primary outcome.
        pass
    return code


def run_status(
    argv: list[str],
    *,
    home: Path | None = None,
    runner: Callable[..., object] = subprocess.run,
    which: Callable[[str], str | None] = shutil.which,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    args = _location_parser("autodev scheduler status").parse_args(argv[1:] if argv and argv[0] == "status" else argv)
    try:
        registration_file, registration = _resolve_registration(
            repo=Path(args.repo),
            github_repo=args.github_repo,
            registration=args.registration,
            home=home,
            runner=runner,
        )
        status = scheduler.scheduler_status(
            Path(args.repo),
            github_repo=args.github_repo,
            registration_file=registration_file,
            home=home,
            runner=runner,
        )
        snapshot = current_health(registration_file, registration, runner=runner, which=which)
        policy = load_notification_policy(registration_file)
        observe_health(registration_file, snapshot, policy=NotificationPolicy())
    except (SchedulerHealthError, scheduler.SchedulerError, issue_queue.QueueError, privacy.PrivacyError) as exc:
        print(str(exc), file=stderr)
        return 2
    if args.json:
        print(
            json.dumps(
                {
                    "scheduler": status.to_json(),
                    "health": snapshot.to_json(),
                    "notifications": policy.to_json(),
                },
                sort_keys=True,
            ),
            file=stdout,
        )
    else:
        print(scheduler._render_status(status), file=stdout)  # type: ignore[attr-defined]
        print(render_health(snapshot), file=stdout)
        print(
            f"Notifications: {policy.backend}"
            + (f"; reminder={policy.reminder_hours}h" if policy.reminder_hours else ""),
            file=stdout,
        )
    return 2 if status.state == "NEEDS_ATTENTION" or snapshot.state == "SCHEDULER_ERROR" else 0


def run_health(
    argv: list[str],
    *,
    home: Path | None = None,
    runner: Callable[..., object] = subprocess.run,
    which: Callable[[str], str | None] = shutil.which,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    args = _location_parser("autodev scheduler health").parse_args(argv[1:] if argv and argv[0] == "health" else argv)
    try:
        registration_file, registration = _resolve_registration(
            repo=Path(args.repo),
            github_repo=args.github_repo,
            registration=args.registration,
            home=home,
            runner=runner,
        )
        snapshot = current_health(registration_file, registration, runner=runner, which=which)
        observe_health(registration_file, snapshot, policy=NotificationPolicy())
    except (SchedulerHealthError, scheduler.SchedulerError, issue_queue.QueueError, privacy.PrivacyError) as exc:
        print(str(exc), file=stderr)
        return 2
    print(json.dumps(snapshot.to_json(), sort_keys=True) if args.json else render_health(snapshot), file=stdout)
    return 2 if snapshot.state == "SCHEDULER_ERROR" else 0


def run_notifications(
    argv: list[str],
    *,
    home: Path | None = None,
    runner: Callable[..., object] = subprocess.run,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    parser = argparse.ArgumentParser(prog="autodev scheduler notifications")
    sub = parser.add_subparsers(dest="action", required=True)
    for name in ("enable", "disable", "status"):
        command = sub.add_parser(name)
        command.add_argument("--repo", default=".")
        command.add_argument("--github-repo", default="")
        command.add_argument("--registration", default="")
        command.add_argument("--json", action="store_true")
        if name == "enable":
            command.add_argument("--reminder-hours", type=int, default=0)
    args = parser.parse_args(argv[1:] if argv and argv[0] == "notifications" else argv)
    try:
        registration_file, _registration = _resolve_registration(
            repo=Path(args.repo),
            github_repo=args.github_repo,
            registration=args.registration,
            home=home,
            runner=runner,
        )
        if args.action == "enable":
            policy = NotificationPolicy(
                backend=NOTIFICATION_NATIVE,
                reminder_hours=args.reminder_hours,
            )
            save_notification_policy(registration_file, policy)
        elif args.action == "disable":
            policy = NotificationPolicy()
            save_notification_policy(registration_file, policy)
        else:
            policy = load_notification_policy(registration_file)
    except (SchedulerHealthError, scheduler.SchedulerError, issue_queue.QueueError) as exc:
        print(str(exc), file=stderr)
        return 2
    if args.json:
        print(json.dumps(policy.to_json(), sort_keys=True), file=stdout)
    else:
        print(
            f"Scheduler notifications: {policy.backend}"
            + (f"; attention reminder every {policy.reminder_hours}h" if policy.reminder_hours else ""),
            file=stdout,
        )
    return 0


def _cleanup_health_state(argv: list[str], *, home: Path | None, runner: Callable[..., object]) -> None:
    try:
        args, _ = _location_parser("autodev scheduler uninstall").parse_known_args(argv[1:])
        registration_file, _registration = _resolve_registration(
            repo=Path(args.repo),
            github_repo=args.github_repo,
            registration=args.registration,
            home=home,
            runner=runner,
        )
    except Exception:
        return
    for path in (health_path(registration_file), notification_path(registration_file)):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def run_cli(
    argv: list[str] | None = None,
    *,
    home: Path | None = None,
    runner: Callable[..., object] = subprocess.run,
    which: Callable[[str], str | None] = shutil.which,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    values = list(argv or [])
    if not values:
        return scheduler.run_cli(values, home=home, runner=runner, which=which, stdout=stdout, stderr=stderr)
    command = values[0]
    if command == "run-once":
        return run_tick(values, home=home, runner=runner, which=which, stdout=stdout, stderr=stderr)
    if command == "status":
        return run_status(values, home=home, runner=runner, which=which, stdout=stdout, stderr=stderr)
    if command == "health":
        return run_health(values, home=home, runner=runner, which=which, stdout=stdout, stderr=stderr)
    if command == "notifications":
        return run_notifications(values, home=home, runner=runner, stdout=stdout, stderr=stderr)
    if command == "uninstall":
        _cleanup_health_state(values, home=home, runner=runner)
    return scheduler.run_cli(values, home=home, runner=runner, which=which, stdout=stdout, stderr=stderr)


if __name__ == "__main__":
    raise SystemExit(run_cli(sys.argv[1:]))
