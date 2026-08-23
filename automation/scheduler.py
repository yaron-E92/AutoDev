from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, TextIO

from automation import (
    distributed_claims,
    issue_queue,
    opencode_entrypoint,
    privacy,
    queue_selection,
    user_install,
    workflow_stages,
)


SCHEDULER_SCHEMA = 1
DEFAULT_CADENCE_MINUTES = 15
MIN_CADENCE_MINUTES = 1
MAX_CADENCE_MINUTES = 59
STATE_ROOT = Path(".autodev") / "schedulers"
WORKER_ROOT = Path(".autodev") / "workers"
REGISTRATION_FILE = "registration.json"
LOCK_FILE = "run.lock"
LOG_FILE = "scheduler.log"
BACKEND_AUTO = "auto"
BACKEND_SYSTEMD = "systemd-user"
BACKEND_CRON = "cron"
BACKEND_WINDOWS = "windows-task"
SUPPORTED_BACKENDS = {BACKEND_SYSTEMD, BACKEND_CRON, BACKEND_WINDOWS}
_REQUIRED_POLICY = (
    Path(".autodev") / "repo.json",
    issue_queue.QUEUE_CONFIG,
    privacy.PRIVACY_CONFIG,
)
_REPO_ID = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class SchedulerError(RuntimeError):
    pass


@dataclass(frozen=True)
class SchedulerRegistration:
    github_repository: str
    source_repository: str
    worker_repository: str
    default_branch: str
    backend: str
    cadence_minutes: int
    launcher: str
    task_id: str
    installed_at: str
    last_run: dict[str, object] | None = None

    def to_json(self) -> dict[str, object]:
        return {
            "schema_version": SCHEDULER_SCHEMA,
            **asdict(self),
        }


@dataclass(frozen=True)
class SchedulerStatus:
    state: str
    github_repository: str = ""
    backend: str = ""
    backend_state: str = ""
    source_repository: str = ""
    worker_repository: str = ""
    worker_exists: bool = False
    cadence_minutes: int = 0
    last_run: dict[str, object] | None = None

    def to_json(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class DispatchResult:
    state: str
    github_repository: str
    selection_state: str = ""
    issue_number: int = 0
    coordinator_exit_code: int | None = None
    coordinator_state: str = ""
    claim_state: str = ""
    claim_worker_id: str = ""
    claim_run_id: str = ""
    detail: str = ""

    def to_json(self) -> dict[str, object]:
        return asdict(self)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _platform_name(value: str | None = None) -> str:
    raw = (value or ("windows" if os.name == "nt" else "posix")).casefold()
    if raw not in {"windows", "posix"}:
        raise SchedulerError(f"unsupported scheduler platform: {raw}")
    return raw


def _repo_parts(github_repo: str) -> tuple[str, str]:
    value = github_repo.strip()
    if not _REPO_ID.fullmatch(value):
        raise SchedulerError("GitHub repository identity must use owner/name format")
    owner, name = value.split("/", 1)
    if owner in {".", ".."} or name in {".", ".."}:
        raise SchedulerError("unsafe GitHub repository identity")
    return owner, name


def registration_path(github_repo: str, *, home: Path | None = None) -> Path:
    owner, name = _repo_parts(github_repo)
    root = (home or Path.home()).expanduser().resolve()
    return root / STATE_ROOT / owner / name / REGISTRATION_FILE


def worker_path(github_repo: str, *, home: Path | None = None) -> Path:
    owner, name = _repo_parts(github_repo)
    root = (home or Path.home()).expanduser().resolve()
    return root / WORKER_ROOT / owner / name


def _task_id(github_repo: str) -> str:
    owner, name = _repo_parts(github_repo)
    raw = f"{owner}-{name}".casefold()
    slug = re.sub(r"[^a-z0-9_.-]+", "-", raw).strip("-.") or "repo"
    return "autodev-" + slug[:80]


def _repo_root(path: Path) -> Path:
    repo = path.expanduser().resolve()
    if not repo.is_dir() or not (repo / ".git").exists():
        raise SchedulerError(f"not a Git repository root: {repo}")
    return repo


def _run_command(
    argv: list[str],
    *,
    runner: Callable[..., object] = subprocess.run,
    cwd: Path | None = None,
    input_text: str | None = None,
) -> object:
    kwargs: dict[str, object] = {
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "capture_output": True,
        "check": False,
    }
    if cwd is not None:
        kwargs["cwd"] = cwd
    if input_text is not None:
        kwargs["input"] = input_text
    try:
        return runner(argv, **kwargs)
    except OSError as exc:
        raise SchedulerError(f"cannot execute {argv[0]}: {exc}") from exc


def _returncode(completed: object) -> int:
    return int(getattr(completed, "returncode", 1))


def _stdout(completed: object) -> str:
    return str(getattr(completed, "stdout", "") or "")


def _stderr(completed: object) -> str:
    return str(getattr(completed, "stderr", "") or "")


def _require_ok(completed: object, argv: list[str]) -> object:
    if _returncode(completed) != 0:
        detail = _stderr(completed).strip() or _stdout(completed).strip() or "no command output"
        raise SchedulerError(
            f"command failed ({_returncode(completed)}): {' '.join(argv)}: {detail}"
        )
    return completed


def _git(
    repo: Path,
    arguments: list[str],
    *,
    runner: Callable[..., object] = subprocess.run,
    check: bool = True,
) -> object:
    argv = ["git", "-C", str(repo), *arguments]
    completed = _run_command(argv, runner=runner)
    return _require_ok(completed, argv) if check else completed


def _git_status(repo: Path, *, runner: Callable[..., object] = subprocess.run) -> str:
    completed = _git(
        repo,
        ["status", "--porcelain", "--untracked-files=normal"],
        runner=runner,
    )
    return _stdout(completed).strip()


def _origin_url(repo: Path, *, runner: Callable[..., object] = subprocess.run) -> str:
    completed = _git(repo, ["remote", "get-url", "origin"], runner=runner)
    value = _stdout(completed).strip()
    if not value:
        raise SchedulerError(f"repository has no usable origin remote: {repo}")
    return value


def _default_branch(repo: Path, *, runner: Callable[..., object] = subprocess.run) -> str:
    symbolic = _git(
        repo,
        ["symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"],
        runner=runner,
        check=False,
    )
    if _returncode(symbolic) == 0:
        value = _stdout(symbolic).strip()
        if value.startswith("origin/") and len(value) > len("origin/"):
            return value[len("origin/") :]
    current = _git(repo, ["branch", "--show-current"], runner=runner)
    value = _stdout(current).strip()
    if not value:
        raise SchedulerError(f"cannot determine worker default branch: {repo}")
    return value


def _policy_bytes(repo: Path) -> dict[str, bytes]:
    values: dict[str, bytes] = {}
    paths = [*_REQUIRED_POLICY, queue_selection.ROADMAP_PATH]
    for relative in paths:
        path = repo / relative
        if path.is_file():
            try:
                values[relative.as_posix()] = path.read_bytes()
            except OSError as exc:
                raise SchedulerError(f"cannot read AutoDev repository policy {path}: {exc}") from exc
    return values


def _validate_source_policy(repo: Path) -> None:
    missing = [str(path) for path in _REQUIRED_POLICY if not (repo / path).is_file()]
    if missing:
        raise SchedulerError(
            "repository is not ready for autonomous scheduling; missing " + ", ".join(missing)
        )
    queue_policy = issue_queue.load_policy(repo)
    if not queue_policy.autonomous_execution:
        raise SchedulerError(
            "repository queue policy disables autonomous_execution; enable it before installing a scheduler"
        )
    distributed_claims.load_claim_policy(repo)
    queue_selection.load_roadmap(repo)
    privacy.load_policy(repo)


def _validate_worker_policy(source: Path, worker: Path) -> None:
    source_values = _policy_bytes(source)
    worker_values = _policy_bytes(worker)
    missing = [key for key in source_values if key not in worker_values]
    mismatched = [
        key
        for key in source_values
        if key in worker_values and source_values[key] != worker_values[key]
    ]
    if missing or mismatched:
        detail = ", ".join([*(f"missing {item}" for item in missing), *(f"different {item}" for item in mismatched)])
        raise SchedulerError(
            "dedicated worker does not contain the configured repository policy ("
            + detail
            + "); commit and push the .autodev configuration before scheduler installation"
        )
    for relative in _REQUIRED_POLICY:
        if not (worker / relative).is_file():
            raise SchedulerError(
                f"dedicated worker is missing committed AutoDev policy {relative}; commit and push repository setup first"
            )


def _ensure_worker(
    source: Path,
    github_repo: str,
    *,
    home: Path | None = None,
    runner: Callable[..., object] = subprocess.run,
) -> tuple[Path, str]:
    worker = worker_path(github_repo, home=home)
    origin = _origin_url(source, runner=runner)
    created = False
    if not worker.exists():
        worker.parent.mkdir(parents=True, exist_ok=True)
        argv = ["git", "clone", "--origin", "origin", origin, str(worker)]
        completed = _run_command(argv, runner=runner)
        try:
            _require_ok(completed, argv)
        except SchedulerError:
            if worker.exists():
                shutil.rmtree(worker, ignore_errors=True)
            raise
        created = True
    if not worker.is_dir() or not (worker / ".git").exists():
        raise SchedulerError(
            f"dedicated worker path exists but is not an AutoDev-managed Git clone: {worker}"
        )
    worker_origin = _origin_url(worker, runner=runner)
    if worker_origin != origin:
        raise SchedulerError(
            f"dedicated worker origin does not match source repository: {worker_origin!r} != {origin!r}; refusing to reuse {worker}"
        )
    if not created and _git_status(worker, runner=runner):
        existing = queue_selection.inspect_existing_run(worker)
        if existing.state == "NONE":
            raise SchedulerError(
                f"dedicated worker contains unexpected local changes: {worker}; AutoDev will not reset or delete them"
            )
    _validate_worker_policy(source, worker)
    return worker, _default_branch(worker, runner=runner)


def _load_registration(path: Path) -> SchedulerRegistration | None:
    path = path.expanduser().resolve()
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SchedulerError(f"invalid scheduler registration: {path}") from exc
    if not isinstance(raw, dict) or raw.get("schema_version") != SCHEDULER_SCHEMA:
        raise SchedulerError(f"unsupported scheduler registration schema: {path}")
    github_repo = str(raw.get("github_repository", ""))
    _repo_parts(github_repo)
    backend = str(raw.get("backend", ""))
    if backend not in SUPPORTED_BACKENDS:
        raise SchedulerError(f"unsupported scheduler backend in {path}: {backend!r}")
    cadence = int(raw.get("cadence_minutes", 0) or 0)
    if not MIN_CADENCE_MINUTES <= cadence <= MAX_CADENCE_MINUTES:
        raise SchedulerError(f"invalid scheduler cadence in {path}: {cadence}")
    last_run = raw.get("last_run")
    return SchedulerRegistration(
        github_repository=github_repo,
        source_repository=str(raw.get("source_repository", "")),
        worker_repository=str(raw.get("worker_repository", "")),
        default_branch=str(raw.get("default_branch", "")),
        backend=backend,
        cadence_minutes=cadence,
        launcher=str(raw.get("launcher", "")),
        task_id=str(raw.get("task_id", "")),
        installed_at=str(raw.get("installed_at", "")),
        last_run=dict(last_run) if isinstance(last_run, dict) else None,
    )


def _write_registration(path: Path, registration: SchedulerRegistration) -> None:
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    try:
        temp.write_text(
            json.dumps(registration.to_json(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temp.replace(path)
    except OSError as exc:
        temp.unlink(missing_ok=True)
        raise SchedulerError(f"cannot write scheduler registration {path}: {exc}") from exc


def _resolve_launcher(
    *,
    home: Path | None = None,
    which: Callable[[str], str | None] = shutil.which,
    explicit: str = "",
) -> str:
    if explicit:
        path = Path(explicit).expanduser().resolve()
        if not path.is_file():
            raise SchedulerError(f"AutoDev launcher does not exist: {path}")
        return str(path)
    direct = which("autodev")
    if direct:
        return str(Path(direct).expanduser().resolve())
    state = user_install.load_install_state(home=home)
    launchers = state.get("launchers", [])
    if isinstance(launchers, list):
        for value in launchers:
            path = Path(str(value)).expanduser().resolve()
            if path.is_file():
                return str(path)
    raise SchedulerError(
        "cannot find the first-class `autodev` launcher; run `autodev install --user --add-to-path` first"
    )


def _systemd_available(
    *,
    runner: Callable[..., object],
    which: Callable[[str], str | None],
) -> bool:
    if not which("systemctl"):
        return False
    completed = _run_command(["systemctl", "--user", "show-environment"], runner=runner)
    return _returncode(completed) == 0


def _select_backend(
    requested: str,
    *,
    platform_name: str | None,
    runner: Callable[..., object],
    which: Callable[[str], str | None],
) -> str:
    platform = _platform_name(platform_name)
    value = requested.casefold().strip() or BACKEND_AUTO
    if value != BACKEND_AUTO and value not in SUPPORTED_BACKENDS:
        raise SchedulerError(f"unsupported scheduler backend: {requested}")
    if platform == "windows":
        if value not in {BACKEND_AUTO, BACKEND_WINDOWS}:
            raise SchedulerError(f"scheduler backend {value} is not supported on Windows")
        if not which("schtasks"):
            raise SchedulerError("Windows Task Scheduler command `schtasks` is not on PATH")
        return BACKEND_WINDOWS
    if value == BACKEND_WINDOWS:
        raise SchedulerError("windows-task backend is only supported on Windows")
    if value == BACKEND_SYSTEMD:
        if not _systemd_available(runner=runner, which=which):
            raise SchedulerError("systemd-user was requested but the user systemd manager is unavailable")
        return BACKEND_SYSTEMD
    if value == BACKEND_CRON:
        if not which("crontab"):
            raise SchedulerError("cron was requested but `crontab` is not on PATH")
        return BACKEND_CRON
    if _systemd_available(runner=runner, which=which):
        return BACKEND_SYSTEMD
    if which("crontab"):
        return BACKEND_CRON
    raise SchedulerError("no supported user scheduler is available (systemd-user or cron)")


def _dispatch_command(registration: SchedulerRegistration, registration_file: Path) -> list[str]:
    return [
        registration.launcher,
        "scheduler",
        "run-once",
        "--registration",
        str(registration_file.expanduser().resolve()),
    ]


def _systemd_quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
    return f'"{escaped}"'


def _systemd_paths(registration: SchedulerRegistration, *, home: Path | None = None) -> tuple[Path, Path]:
    root = (home or Path.home()).expanduser().resolve() / ".config" / "systemd" / "user"
    return root / f"{registration.task_id}.service", root / f"{registration.task_id}.timer"


def _install_systemd(
    registration: SchedulerRegistration,
    registration_file: Path,
    *,
    home: Path | None,
    runner: Callable[..., object],
) -> None:
    service, timer = _systemd_paths(registration, home=home)
    service.parent.mkdir(parents=True, exist_ok=True)
    command = " ".join(_systemd_quote(item) for item in _dispatch_command(registration, registration_file))
    path_value = os.environ.get("PATH", "").strip()
    environment = f"Environment={_systemd_quote('PATH=' + path_value)}\n" if path_value else ""
    service.write_text(
        "[Unit]\n"
        f"Description=AutoDev autonomous run for {registration.github_repository}\n\n"
        "[Service]\n"
        "Type=oneshot\n"
        f"{environment}"
        f"ExecStart={command}\n",
        encoding="utf-8",
    )
    timer.write_text(
        "[Unit]\n"
        f"Description=AutoDev scheduler for {registration.github_repository}\n\n"
        "[Timer]\n"
        "OnBootSec=2min\n"
        f"OnUnitActiveSec={registration.cadence_minutes}min\n"
        "Persistent=true\n"
        f"Unit={registration.task_id}.service\n\n"
        "[Install]\n"
        "WantedBy=timers.target\n",
        encoding="utf-8",
    )
    for argv in (
        ["systemctl", "--user", "daemon-reload"],
        ["systemctl", "--user", "enable", "--now", f"{registration.task_id}.timer"],
    ):
        _require_ok(_run_command(argv, runner=runner), argv)


def _cron_markers(task_id: str) -> tuple[str, str]:
    return (
        f"# >>> AutoDev scheduler {task_id} >>>",
        f"# <<< AutoDev scheduler {task_id} <<<",
    )


def _read_crontab(*, runner: Callable[..., object]) -> str:
    argv = ["crontab", "-l"]
    completed = _run_command(argv, runner=runner)
    if _returncode(completed) == 0:
        return _stdout(completed)
    detail = (_stderr(completed) + "\n" + _stdout(completed)).casefold()
    if _returncode(completed) == 1 and ("no crontab" in detail or not detail.strip()):
        return ""
    _require_ok(completed, argv)
    return ""


def _remove_cron_block(text: str, task_id: str) -> str:
    begin, end = _cron_markers(task_id)
    lines = text.splitlines()
    output: list[str] = []
    skipping = False
    for line in lines:
        if line.strip() == begin:
            skipping = True
            continue
        if skipping and line.strip() == end:
            skipping = False
            continue
        if not skipping:
            output.append(line)
    return "\n".join(output).rstrip()


def _cron_command(registration: SchedulerRegistration, registration_file: Path) -> str:
    command = shlex.join(_dispatch_command(registration, registration_file))
    command = command.replace("%", "\\%")
    path_value = os.environ.get("PATH", "").strip()
    prefix = f"PATH={shlex.quote(path_value)} " if path_value else ""
    log_path = registration_file.parent / LOG_FILE
    return (
        f"*/{registration.cadence_minutes} * * * * {prefix}{command} "
        f">> {shlex.quote(str(log_path))} 2>&1"
    )


def _install_cron(
    registration: SchedulerRegistration,
    registration_file: Path,
    *,
    runner: Callable[..., object],
) -> None:
    current = _read_crontab(runner=runner)
    clean = _remove_cron_block(current, registration.task_id)
    begin, end = _cron_markers(registration.task_id)
    block = "\n".join([begin, _cron_command(registration, registration_file), end])
    updated = (clean + "\n\n" if clean else "") + block + "\n"
    argv = ["crontab", "-"]
    _require_ok(_run_command(argv, runner=runner, input_text=updated), argv)


def _windows_task_action(registration: SchedulerRegistration, registration_file: Path) -> str:
    inner = subprocess.list2cmdline(_dispatch_command(registration, registration_file))
    return subprocess.list2cmdline(["cmd.exe", "/d", "/s", "/c", inner])


def _install_windows_task(
    registration: SchedulerRegistration,
    registration_file: Path,
    *,
    runner: Callable[..., object],
) -> None:
    argv = [
        "schtasks",
        "/Create",
        "/TN",
        registration.task_id,
        "/SC",
        "MINUTE",
        "/MO",
        str(registration.cadence_minutes),
        "/TR",
        _windows_task_action(registration, registration_file),
        "/F",
    ]
    _require_ok(_run_command(argv, runner=runner), argv)


def _install_backend(
    registration: SchedulerRegistration,
    registration_file: Path,
    *,
    home: Path | None,
    runner: Callable[..., object],
) -> None:
    if registration.backend == BACKEND_SYSTEMD:
        _install_systemd(registration, registration_file, home=home, runner=runner)
        return
    if registration.backend == BACKEND_CRON:
        _install_cron(registration, registration_file, runner=runner)
        return
    if registration.backend == BACKEND_WINDOWS:
        _install_windows_task(registration, registration_file, runner=runner)
        return
    raise SchedulerError(f"unsupported scheduler backend: {registration.backend}")


def _uninstall_backend(
    registration: SchedulerRegistration,
    *,
    home: Path | None,
    runner: Callable[..., object],
) -> None:
    if registration.backend == BACKEND_SYSTEMD:
        service, timer = _systemd_paths(registration, home=home)
        argv = ["systemctl", "--user", "disable", "--now", timer.name]
        _run_command(argv, runner=runner)
        service.unlink(missing_ok=True)
        timer.unlink(missing_ok=True)
        reload_argv = ["systemctl", "--user", "daemon-reload"]
        _run_command(reload_argv, runner=runner)
        return
    if registration.backend == BACKEND_CRON:
        current = _read_crontab(runner=runner)
        updated = _remove_cron_block(current, registration.task_id)
        if updated:
            updated += "\n"
            argv = ["crontab", "-"]
            _require_ok(_run_command(argv, runner=runner, input_text=updated), argv)
        elif current:
            argv = ["crontab", "-"]
            _require_ok(_run_command(argv, runner=runner, input_text=""), argv)
        return
    if registration.backend == BACKEND_WINDOWS:
        argv = ["schtasks", "/Delete", "/TN", registration.task_id, "/F"]
        _run_command(argv, runner=runner)
        return
    raise SchedulerError(f"unsupported scheduler backend: {registration.backend}")


def _backend_state(
    registration: SchedulerRegistration,
    *,
    home: Path | None,
    runner: Callable[..., object],
) -> str:
    if registration.backend == BACKEND_SYSTEMD:
        _service, timer = _systemd_paths(registration, home=home)
        if not timer.is_file():
            return "missing"
        enabled = _run_command(
            ["systemctl", "--user", "is-enabled", timer.name],
            runner=runner,
        )
        active = _run_command(
            ["systemctl", "--user", "is-active", timer.name],
            runner=runner,
        )
        if _returncode(enabled) == 0 and _returncode(active) == 0:
            return "active"
        if _returncode(enabled) == 0:
            return "enabled-inactive"
        return "disabled"
    if registration.backend == BACKEND_CRON:
        current = _read_crontab(runner=runner)
        begin, end = _cron_markers(registration.task_id)
        return "active" if begin in current and end in current else "missing"
    if registration.backend == BACKEND_WINDOWS:
        completed = _run_command(
            ["schtasks", "/Query", "/TN", registration.task_id],
            runner=runner,
        )
        return "active" if _returncode(completed) == 0 else "missing"
    return "unsupported"


def install_scheduler(
    repo: Path,
    *,
    github_repo: str = "",
    backend: str = BACKEND_AUTO,
    cadence_minutes: int = DEFAULT_CADENCE_MINUTES,
    launcher: str = "",
    home: Path | None = None,
    platform_name: str | None = None,
    runner: Callable[..., object] = subprocess.run,
    which: Callable[[str], str | None] = shutil.which,
) -> SchedulerRegistration:
    source = _repo_root(repo)
    _validate_source_policy(source)
    if not MIN_CADENCE_MINUTES <= cadence_minutes <= MAX_CADENCE_MINUTES:
        raise SchedulerError(
            f"cadence must be between {MIN_CADENCE_MINUTES} and {MAX_CADENCE_MINUTES} minutes"
        )
    resolved = issue_queue.resolve_github_repo(
        source,
        explicit=github_repo,
        runner=runner,
    )
    path = registration_path(resolved, home=home)
    existing = _load_registration(path)
    selected_backend = _select_backend(
        existing.backend if existing and backend == BACKEND_AUTO else backend,
        platform_name=platform_name,
        runner=runner,
        which=which,
    )
    resolved_launcher = _resolve_launcher(home=home, which=which, explicit=launcher)
    worker, default_branch = _ensure_worker(
        source,
        resolved,
        home=home,
        runner=runner,
    )
    distributed_claims.worker_identity(home=home)
    registration = SchedulerRegistration(
        github_repository=resolved,
        source_repository=str(source),
        worker_repository=str(worker),
        default_branch=default_branch,
        backend=selected_backend,
        cadence_minutes=cadence_minutes,
        launcher=resolved_launcher,
        task_id=_task_id(resolved),
        installed_at=existing.installed_at if existing and existing.installed_at else _now(),
        last_run=existing.last_run if existing else None,
    )
    if existing and existing.backend != selected_backend:
        _uninstall_backend(existing, home=home, runner=runner)
    _write_registration(path, registration)
    try:
        _install_backend(registration, path, home=home, runner=runner)
    except Exception:
        if existing:
            _write_registration(path, existing)
        else:
            path.unlink(missing_ok=True)
        raise
    return registration


def scheduler_status(
    repo: Path | None = None,
    *,
    github_repo: str = "",
    registration_file: Path | None = None,
    home: Path | None = None,
    runner: Callable[..., object] = subprocess.run,
) -> SchedulerStatus:
    path: Path
    resolved = github_repo.strip()
    if registration_file is not None:
        path = registration_file.expanduser().resolve()
    else:
        if not resolved:
            if repo is None:
                raise SchedulerError("repository is required to resolve scheduler status")
            source = _repo_root(repo)
            resolved = issue_queue.resolve_github_repo(source, runner=runner)
        path = registration_path(resolved, home=home)
    registration = _load_registration(path)
    if registration is None:
        return SchedulerStatus(state="NOT_INSTALLED", github_repository=resolved)
    backend_state = _backend_state(registration, home=home, runner=runner)
    worker = Path(registration.worker_repository).expanduser()
    return SchedulerStatus(
        state="INSTALLED" if backend_state == "active" else "NEEDS_ATTENTION",
        github_repository=registration.github_repository,
        backend=registration.backend,
        backend_state=backend_state,
        source_repository=registration.source_repository,
        worker_repository=registration.worker_repository,
        worker_exists=worker.is_dir() and (worker / ".git").exists(),
        cadence_minutes=registration.cadence_minutes,
        last_run=registration.last_run,
    )


def uninstall_scheduler(
    repo: Path | None = None,
    *,
    github_repo: str = "",
    registration_file: Path | None = None,
    home: Path | None = None,
    runner: Callable[..., object] = subprocess.run,
) -> SchedulerStatus:
    path: Path
    resolved = github_repo.strip()
    if registration_file is not None:
        path = registration_file.expanduser().resolve()
    else:
        if not resolved:
            if repo is None:
                raise SchedulerError("repository is required to uninstall scheduler")
            source = _repo_root(repo)
            resolved = issue_queue.resolve_github_repo(source, runner=runner)
        path = registration_path(resolved, home=home)
    registration = _load_registration(path)
    if registration is None:
        return SchedulerStatus(state="NOT_INSTALLED", github_repository=resolved)
    _uninstall_backend(registration, home=home, runner=runner)
    path.unlink(missing_ok=True)
    return SchedulerStatus(
        state="UNINSTALLED",
        github_repository=registration.github_repository,
        backend=registration.backend,
        backend_state="removed",
        source_repository=registration.source_repository,
        worker_repository=registration.worker_repository,
        worker_exists=Path(registration.worker_repository).is_dir(),
        cadence_minutes=registration.cadence_minutes,
        last_run=registration.last_run,
    )


class SchedulerLock:
    def __init__(self, path: Path):
        self.path = path.expanduser().resolve()
        self.file: object | None = None
        self.acquired = False
        self._windows = os.name == "nt"

    def __enter__(self) -> "SchedulerLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        file = open(self.path, "a+b")
        self.file = file
        if self._windows:
            import msvcrt

            file.seek(0, os.SEEK_END)
            if file.tell() == 0:
                file.write(b"0")
                file.flush()
            file.seek(0)
            try:
                msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
                self.acquired = True
            except OSError:
                self.acquired = False
            return self

        import fcntl

        try:
            fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.acquired = True
        except OSError:
            self.acquired = False
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        file = self.file
        if file is None:
            return
        try:
            if self.acquired:
                if self._windows:
                    import msvcrt

                    file.seek(0)
                    msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(file.fileno(), fcntl.LOCK_UN)
        finally:
            file.close()
            self.file = None
            self.acquired = False


def _prepare_worker(
    registration: SchedulerRegistration,
    *,
    runner: Callable[..., object],
) -> queue_selection.ExistingRun:
    worker = Path(registration.worker_repository).expanduser().resolve()
    if not worker.is_dir() or not (worker / ".git").exists():
        raise SchedulerError(f"dedicated scheduler worker is missing or invalid: {worker}")
    fetch = ["fetch", "--prune", "origin"]
    _git(worker, fetch, runner=runner)
    existing = queue_selection.inspect_existing_run(worker)
    if existing.state != "NONE":
        return existing
    dirty = _git_status(worker, runner=runner)
    if dirty:
        raise SchedulerError(
            f"dedicated worker contains unexpected local changes: {worker}; refusing to reset or delete them"
        )
    _git(worker, ["checkout", registration.default_branch], runner=runner)
    _git(
        worker,
        ["merge", "--ff-only", f"origin/{registration.default_branch}"],
        runner=runner,
    )
    dirty = _git_status(worker, runner=runner)
    if dirty:
        raise SchedulerError(
            f"dedicated worker became dirty while updating {registration.default_branch}: {worker}"
        )
    return existing


def _coordinator_state(worker: Path) -> str:
    current = worker / workflow_stages.CURRENT_DIR
    if not current.is_dir():
        return ""
    try:
        state = workflow_stages.read_state(current)
    except Exception:
        return ""
    return str(state.get("Status", "") or state.get("QueueState", ""))


def _record_last_run(
    path: Path,
    registration: SchedulerRegistration,
    result: DispatchResult,
    *,
    started_at: str,
) -> None:
    latest = SchedulerRegistration(
        github_repository=registration.github_repository,
        source_repository=registration.source_repository,
        worker_repository=registration.worker_repository,
        default_branch=registration.default_branch,
        backend=registration.backend,
        cadence_minutes=registration.cadence_minutes,
        launcher=registration.launcher,
        task_id=registration.task_id,
        installed_at=registration.installed_at,
        last_run={
            "started_at": started_at,
            "finished_at": _now(),
            **result.to_json(),
        },
    )
    _write_registration(path, latest)


def _invoke_headless(
    argv: list[str],
    *,
    coordinator: Callable[[list[str]], int],
) -> int:
    previous_headless = os.environ.get("AUTODEV_HEADLESS")
    previous_interactive = os.environ.pop("AUTODEV_INTERACTIVE_CONSENT", None)
    os.environ["AUTODEV_HEADLESS"] = "1"
    try:
        return int(coordinator(argv))
    finally:
        if previous_headless is None:
            os.environ.pop("AUTODEV_HEADLESS", None)
        else:
            os.environ["AUTODEV_HEADLESS"] = previous_headless
        if previous_interactive is not None:
            os.environ["AUTODEV_INTERACTIVE_CONSENT"] = previous_interactive


def _claim_terminal_state(coordinator_state: str) -> bool:
    normalized = coordinator_state.casefold().replace("_", "").replace("-", "")
    return normalized in {
        "readyforreview",
        "prready",
        "attentionrequired",
        "attention",
        "blocked",
        "failed",
        "terminalfailed",
    }


def _dispatch_state(coordinator_state: str) -> str:
    normalized = coordinator_state.casefold().replace("_", "").replace("-", "")
    if normalized in {"readyforreview", "prready"}:
        return "PR_READY"
    if normalized in {"attentionrequired", "attention"}:
        return "ATTENTION_REQUIRED"
    if normalized in {"blocked", "failed", "terminalfailed"}:
        return "RUN_HEALTH_BLOCKED"
    return "DISPATCHED"


def _capacity_result(
    registration: SchedulerRegistration,
    *,
    started_at: str,
    path: Path,
    occupied: int,
    maximum: int,
    stdout: TextIO,
) -> int:
    result = DispatchResult(
        state="NO_CAPACITY",
        github_repository=registration.github_repository,
        detail=f"distributed claim capacity is full ({occupied}/{maximum})",
    )
    _record_last_run(path, registration, result, started_at=started_at)
    print(json.dumps(result.to_json(), sort_keys=True), file=stdout)
    return 0


def run_once(
    registration_file: Path,
    *,
    home: Path | None = None,
    runner: Callable[..., object] = subprocess.run,
    coordinator: Callable[[list[str]], int] = opencode_entrypoint.run,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
    claiming_enabled: bool = True,
) -> int:
    path = registration_file.expanduser().resolve()
    registration = _load_registration(path)
    if registration is None:
        raise SchedulerError(f"scheduler is not installed: {path}")
    started_at = _now()
    lock_path = path.parent / LOCK_FILE
    with SchedulerLock(lock_path) as lock:
        if not lock.acquired:
            result = DispatchResult(
                state="OVERLAP_SUPPRESSED",
                github_repository=registration.github_repository,
                detail="another scheduler tick already owns this repository on this machine",
            )
            print(json.dumps(result.to_json(), sort_keys=True), file=stdout)
            return 0

        worker = Path(registration.worker_repository).expanduser().resolve()
        prepared_existing = _prepare_worker(registration, runner=runner)
        claim_policy = distributed_claims.ClaimPolicy()
        worker_id = ""
        excluded: set[int] = set()
        if claiming_enabled:
            claim_policy = distributed_claims.load_claim_policy(worker)
            worker_id = distributed_claims.worker_identity(home=home).worker_id
            distributed_claims.reconcile_stale_claims(
                worker,
                registration.github_repository,
                runner=runner,
            )
            shared_claims = distributed_claims.list_claims(worker, runner=runner)
            if prepared_existing.state == "NONE":
                excluded.update(item.issue_number for item in shared_claims)
                if len(shared_claims) >= claim_policy.max_concurrent_issues:
                    return _capacity_result(
                        registration,
                        started_at=started_at,
                        path=path,
                        occupied=len(shared_claims),
                        maximum=claim_policy.max_concurrent_issues,
                        stdout=stdout,
                    )

        selection: queue_selection.SelectionResult
        claim: distributed_claims.Claim | None = None
        claim_state = ""
        while True:
            selection = queue_selection.select_next(
                worker,
                registration.github_repository,
                runner=runner,
                excluded_issue_numbers=excluded,
            )
            if selection.state != "SELECTED" or not claiming_enabled:
                break
            attempt = distributed_claims.acquire_claim(
                worker,
                registration.github_repository,
                selection.issue_number,
                worker_id,
                f"origin/{registration.default_branch}",
                policy=claim_policy,
                runner=runner,
            )
            if attempt.claim is not None and attempt.state in {"ACQUIRED", "OWNED"}:
                claim = attempt.claim
                claim_state = attempt.state
                break
            excluded.add(selection.issue_number)
            shared_claims = distributed_claims.list_claims(worker, runner=runner)
            if len(shared_claims) >= claim_policy.max_concurrent_issues:
                return _capacity_result(
                    registration,
                    started_at=started_at,
                    path=path,
                    occupied=len(shared_claims),
                    maximum=claim_policy.max_concurrent_issues,
                    stdout=stdout,
                )

        if selection.state == "NO_READY_WORK":
            result = DispatchResult(
                state="NO_READY_WORK",
                github_repository=registration.github_repository,
                selection_state=selection.state,
                detail=selection.explanation,
            )
            _record_last_run(path, registration, result, started_at=started_at)
            print(json.dumps(result.to_json(), sort_keys=True), file=stdout)
            return 0
        if selection.state == "ATTENTION_REQUIRED":
            result = DispatchResult(
                state="ATTENTION_REQUIRED",
                github_repository=registration.github_repository,
                selection_state=selection.state,
                issue_number=selection.issue_number,
                detail=selection.explanation,
            )
            _record_last_run(path, registration, result, started_at=started_at)
            print(json.dumps(result.to_json(), sort_keys=True), file=stdout)
            return 0
        if selection.state == "RUN_HEALTH_BLOCKED":
            result = DispatchResult(
                state="RUN_HEALTH_BLOCKED",
                github_repository=registration.github_repository,
                selection_state=selection.state,
                issue_number=selection.issue_number,
                detail=selection.explanation,
            )
            _record_last_run(path, registration, result, started_at=started_at)
            print(json.dumps(result.to_json(), sort_keys=True), file=stderr)
            return 2

        if claiming_enabled and selection.state == "RESUME_EXISTING":
            attempt = distributed_claims.acquire_claim(
                worker,
                registration.github_repository,
                selection.issue_number,
                worker_id,
                f"origin/{registration.default_branch}",
                policy=claim_policy,
                runner=runner,
            )
            if attempt.claim is None or attempt.state not in {"ACQUIRED", "OWNED"}:
                owner = attempt.owner.worker_id if attempt.owner is not None else "another worker"
                result = DispatchResult(
                    state="CLAIM_CONFLICT",
                    github_repository=registration.github_repository,
                    selection_state=selection.state,
                    issue_number=selection.issue_number,
                    claim_state=attempt.state,
                    detail=f"local durable run cannot resume because issue is claimed by {owner}",
                )
                _record_last_run(path, registration, result, started_at=started_at)
                print(json.dumps(result.to_json(), sort_keys=True), file=stderr)
                return 2
            claim = attempt.claim
            claim_state = attempt.state

        if selection.state == "RESUME_EXISTING":
            coordinate_argv = ["coordinate", "--repo", str(worker), "--resume"]
        elif selection.state == "SELECTED":
            coordinate_argv = [
                "coordinate",
                "--repo",
                str(worker),
                "--arguments",
                str(selection.issue_number),
            ]
        else:
            raise SchedulerError(f"unsupported queue selection state: {selection.state}")

        if claim is None:
            code = _invoke_headless(coordinate_argv, coordinator=coordinator)
            latest_claim = None
            claim_lost = False
        else:
            with distributed_claims.HeartbeatLease(worker, claim, runner=runner) as lease:
                code = _invoke_headless(coordinate_argv, coordinator=coordinator)
            latest_claim = lease.latest_claim()
            claim_lost = lease.lost

        coordinator_state = _coordinator_state(worker)
        if claim_lost:
            result = DispatchResult(
                state="CLAIM_CONFLICT",
                github_repository=registration.github_repository,
                selection_state=selection.state,
                issue_number=selection.issue_number,
                coordinator_exit_code=code,
                coordinator_state=coordinator_state,
                claim_state="LOST",
                claim_worker_id=worker_id,
                claim_run_id=latest_claim.run_id if latest_claim else "",
                detail="distributed claim ownership changed while the coordinator was active",
            )
            _record_last_run(path, registration, result, started_at=started_at)
            print(json.dumps(result.to_json(), sort_keys=True), file=stderr)
            return 2

        dispatch_state = _dispatch_state(coordinator_state)
        release_error = False
        if latest_claim is not None and _claim_terminal_state(coordinator_state):
            release_error = not distributed_claims.release_claim(
                worker,
                latest_claim,
                runner=runner,
            )

        if release_error:
            result = DispatchResult(
                state="CLAIM_RELEASE_FAILED",
                github_repository=registration.github_repository,
                selection_state=selection.state,
                issue_number=selection.issue_number,
                coordinator_exit_code=code,
                coordinator_state=coordinator_state,
                claim_state="RELEASE_FAILED",
                claim_worker_id=worker_id,
                claim_run_id=latest_claim.run_id if latest_claim else "",
                detail="terminal coordinator state was reached but distributed claim release lost its compare-and-swap",
            )
            _record_last_run(path, registration, result, started_at=started_at)
            print(json.dumps(result.to_json(), sort_keys=True), file=stderr)
            return 2

        result = DispatchResult(
            state=dispatch_state,
            github_repository=registration.github_repository,
            selection_state=selection.state,
            issue_number=selection.issue_number,
            coordinator_exit_code=code,
            coordinator_state=coordinator_state,
            claim_state=("RELEASED" if latest_claim is not None and _claim_terminal_state(coordinator_state) else claim_state),
            claim_worker_id=worker_id if latest_claim is not None else "",
            claim_run_id=latest_claim.run_id if latest_claim is not None else "",
            detail=(
                "coordinator returned a successful non-runnable attention state"
                if dispatch_state == "ATTENTION_REQUIRED"
                else "existing AutoDev coordinator completed this scheduler dispatch"
            ),
        )
        _record_last_run(path, registration, result, started_at=started_at)
        print(json.dumps(result.to_json(), sort_keys=True), file=stdout if code == 0 else stderr)
        return code


def doctor_state(
    repo: Path,
    github_repo: str,
    *,
    home: Path | None = None,
    runner: Callable[..., object] = subprocess.run,
) -> tuple[str, str]:
    path = registration_path(github_repo, home=home)
    registration = _load_registration(path)
    if registration is None:
        return "info", "scheduler not installed; autonomous scheduling remains explicit opt-in"
    if Path(registration.source_repository).expanduser().resolve() != repo.expanduser().resolve():
        return (
            "error",
            f"scheduler for {github_repo} is registered from a different source checkout: {registration.source_repository}",
        )
    worker = Path(registration.worker_repository).expanduser()
    if not worker.is_dir() or not (worker / ".git").exists():
        return "error", f"scheduler worker is missing: {worker}"
    try:
        identity = distributed_claims.worker_identity(home=home)
        policy = distributed_claims.load_claim_policy(worker)
    except distributed_claims.ClaimError as exc:
        return "error", f"distributed claim configuration is invalid: {exc}"
    backend = _backend_state(registration, home=home, runner=runner)
    if backend != "active":
        return "error", f"scheduler backend {registration.backend} is {backend}"
    return (
        "ok",
        f"{registration.backend} active every {registration.cadence_minutes} minute(s); "
        f"worker={worker}; worker-id={identity.worker_id}; max-concurrency={policy.max_concurrent_issues}",
    )


def _render_status(status: SchedulerStatus) -> str:
    if status.state == "NOT_INSTALLED":
        suffix = f" for {status.github_repository}" if status.github_repository else ""
        return f"AutoDev scheduler is not installed{suffix}."
    lines = [
        f"AutoDev scheduler: {status.state}",
        f"repository={status.github_repository}",
        f"backend={status.backend} ({status.backend_state})",
        f"cadence={status.cadence_minutes} minute(s)",
        f"worker={status.worker_repository}",
    ]
    if status.last_run:
        lines.append(f"last-run={status.last_run.get('state', '')}")
    return "\n".join(lines)


def run_cli(
    argv: list[str] | None = None,
    *,
    home: Path | None = None,
    platform_name: str | None = None,
    runner: Callable[..., object] = subprocess.run,
    which: Callable[[str], str | None] = shutil.which,
    coordinator: Callable[[list[str]], int] = opencode_entrypoint.run,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    parser = argparse.ArgumentParser(prog="autodev scheduler")
    sub = parser.add_subparsers(dest="command", required=True)

    install_parser = sub.add_parser("install")
    install_parser.add_argument("--repo", default=".")
    install_parser.add_argument("--github-repo", default="")
    install_parser.add_argument(
        "--backend",
        default=BACKEND_AUTO,
        choices=[BACKEND_AUTO, BACKEND_SYSTEMD, BACKEND_CRON, BACKEND_WINDOWS],
    )
    install_parser.add_argument("--cadence-minutes", type=int, default=DEFAULT_CADENCE_MINUTES)
    install_parser.add_argument("--launcher", default="")
    install_parser.add_argument("--json", action="store_true")

    status_parser = sub.add_parser("status")
    status_parser.add_argument("--repo", default=".")
    status_parser.add_argument("--github-repo", default="")
    status_parser.add_argument("--registration", default="")
    status_parser.add_argument("--json", action="store_true")

    run_parser = sub.add_parser("run-once")
    run_parser.add_argument("--repo", default=".")
    run_parser.add_argument("--github-repo", default="")
    run_parser.add_argument("--registration", default="")
    run_parser.add_argument("--json", action="store_true")

    uninstall_parser = sub.add_parser("uninstall")
    uninstall_parser.add_argument("--repo", default=".")
    uninstall_parser.add_argument("--github-repo", default="")
    uninstall_parser.add_argument("--registration", default="")
    uninstall_parser.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)
    try:
        if args.command == "install":
            registration = install_scheduler(
                Path(args.repo),
                github_repo=args.github_repo,
                backend=args.backend,
                cadence_minutes=args.cadence_minutes,
                launcher=args.launcher,
                home=home,
                platform_name=platform_name,
                runner=runner,
                which=which,
            )
            payload = {"state": "INSTALLED", **registration.to_json()}
            print(
                json.dumps(payload, sort_keys=True)
                if args.json
                else (
                    f"Installed {registration.backend} AutoDev scheduler for "
                    f"{registration.github_repository}; worker={registration.worker_repository}."
                ),
                file=stdout,
            )
            return 0

        registration_file = Path(args.registration) if args.registration else None
        if args.command == "status":
            status = scheduler_status(
                Path(args.repo),
                github_repo=args.github_repo,
                registration_file=registration_file,
                home=home,
                runner=runner,
            )
            print(
                json.dumps(status.to_json(), sort_keys=True) if args.json else _render_status(status),
                file=stdout,
            )
            return 0 if status.state != "NEEDS_ATTENTION" else 2

        if args.command == "uninstall":
            status = uninstall_scheduler(
                Path(args.repo),
                github_repo=args.github_repo,
                registration_file=registration_file,
                home=home,
                runner=runner,
            )
            print(
                json.dumps(status.to_json(), sort_keys=True) if args.json else _render_status(status),
                file=stdout,
            )
            return 0

        path = registration_file
        if path is None:
            source = _repo_root(Path(args.repo))
            resolved = issue_queue.resolve_github_repo(
                source,
                explicit=args.github_repo,
                runner=runner,
            )
            path = registration_path(resolved, home=home)
        return run_once(
            path,
            home=home,
            runner=runner,
            coordinator=coordinator,
            stdout=stdout,
            stderr=stderr,
        )
    except (
        SchedulerError,
        distributed_claims.ClaimError,
        issue_queue.QueueError,
        queue_selection.RoadmapError,
        privacy.PrivacyError,
    ) as exc:
        print(str(exc), file=stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(run_cli())
