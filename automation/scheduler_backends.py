from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path
from typing import Callable

from automation.scheduler_process import (
    _read_crontab if False else _run_command,
)
from automation.scheduler_process import _require_ok, _returncode, _run_command, _stderr, _stdout
from automation.scheduler_types import (
    BACKEND_AUTO,
    BACKEND_CRON,
    BACKEND_SYSTEMD,
    BACKEND_WINDOWS,
    LOG_FILE,
    SUPPORTED_BACKENDS,
    SchedulerError,
    SchedulerRegistration,
    _platform_name,
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
