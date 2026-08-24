from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable, TextIO
from automation import issue_queue, privacy, privacy_grants, queue_selection, scheduler, workflow_stages

from automation.scheduler_health_contract import (
    HealthSnapshot,
    SchedulerHealthError,
    _iso,
    _now,
)
from automation.scheduler_health_notifications import (
    observe_health,
)
from automation.scheduler_health_probes import (
    _fingerprint,
    _fingerprint_source,
    compute_health,
)

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
