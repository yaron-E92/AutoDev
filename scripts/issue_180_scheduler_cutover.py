from __future__ import annotations

from pathlib import Path


SCHEDULER = Path("automation/scheduler.py")
REGISTRATION = Path("automation/scheduler_registration.py")

HEADER = '''from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable, TextIO

from automation import (
    distributed_claims,
    issue_queue,
    opencode_entrypoint,
    privacy,
    queue_selection,
    workflow_stages,
)
from automation.scheduler_backends import (
    _backend_state,
    _cron_command,
    _cron_markers,
    _dispatch_command,
    _install_backend,
    _install_cron,
    _install_systemd,
    _install_windows_task,
    _read_crontab,
    _remove_cron_block,
    _select_backend,
    _systemd_available,
    _systemd_paths,
    _systemd_quote,
    _uninstall_backend,
    _windows_task_action,
)
from automation.scheduler_process import (
    _default_branch,
    _git,
    _git_status,
    _origin_url,
    _require_ok,
    _returncode,
    _run_command,
    _stderr,
    _stdout,
)
from automation.scheduler_registration import (
    _ensure_worker,
    _load_registration,
    _policy_bytes,
    _resolve_launcher,
    _validate_source_policy,
    _validate_worker_policy,
    _write_registration,
    install_scheduler,
    scheduler_status,
    uninstall_scheduler,
)
from automation.scheduler_types import (
    BACKEND_AUTO,
    BACKEND_CRON,
    BACKEND_SYSTEMD,
    BACKEND_WINDOWS,
    DEFAULT_CADENCE_MINUTES,
    LOCK_FILE,
    LOG_FILE,
    MAX_CADENCE_MINUTES,
    MIN_CADENCE_MINUTES,
    REGISTRATION_FILE,
    SCHEDULER_SCHEMA,
    STATE_ROOT,
    SUPPORTED_BACKENDS,
    WORKER_ROOT,
    DispatchResult,
    SchedulerError,
    SchedulerRegistration,
    SchedulerStatus,
    _now,
    _platform_name,
    _repo_parts,
    _repo_root,
    _task_id,
    registration_path,
    worker_path,
)


'''


def cut_scheduler() -> None:
    text = SCHEDULER.read_text(encoding="utf-8")
    marker = "class SchedulerLock:"
    if text.count(marker) != 1:
        raise SystemExit(f"expected exactly one {marker!r} marker")
    tail = marker + text.split(marker, 1)[1]
    SCHEDULER.write_text(HEADER + tail, encoding="utf-8")


def preserve_install_signature() -> None:
    text = REGISTRATION.read_text(encoding="utf-8")
    import_marker = "    BACKEND_AUTO,\n    MAX_CADENCE_MINUTES,"
    if import_marker in text:
        text = text.replace(
            import_marker,
            "    BACKEND_AUTO,\n    DEFAULT_CADENCE_MINUTES,\n    MAX_CADENCE_MINUTES,",
            1,
        )
    signature_marker = "    cadence_minutes: int,\n"
    if signature_marker not in text:
        if "cadence_minutes: int = DEFAULT_CADENCE_MINUTES" not in text:
            raise SystemExit("could not find install_scheduler cadence signature")
    else:
        text = text.replace(
            signature_marker,
            "    cadence_minutes: int = DEFAULT_CADENCE_MINUTES,\n",
            1,
        )
    REGISTRATION.write_text(text, encoding="utf-8")


def main() -> None:
    cut_scheduler()
    preserve_install_signature()


if __name__ == "__main__":
    main()
