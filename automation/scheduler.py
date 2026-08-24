from __future__ import annotations

from automation import claim_contract, claim_identity, claim_lease, claim_recovery, claim_repository, queue_contract, queue_github

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable, TextIO

from automation import opencode_entrypoint, privacy, queue_selection, workflow_stages
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
        claim_policy = claim_contract.ClaimPolicy()
        worker_id = ""
        excluded: set[int] = set()
        if claiming_enabled:
            claim_policy = claim_identity.load_claim_policy(worker)
            worker_id = claim_identity.worker_identity(home=home).worker_id
            claim_recovery.reconcile_stale_claims(
                worker,
                registration.github_repository,
                runner=runner,
            )
            shared_claims = claim_repository.list_claims(worker, runner=runner)
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
        claim: claim_contract.Claim | None = None
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
            attempt = claim_lease.acquire_claim(
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
            shared_claims = claim_repository.list_claims(worker, runner=runner)
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
            attempt = claim_lease.acquire_claim(
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
            with claim_lease.HeartbeatLease(worker, claim, runner=runner) as lease:
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
            release_error = not claim_lease.release_claim(
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
        identity = claim_identity.worker_identity(home=home)
        policy = claim_identity.load_claim_policy(worker)
    except claim_contract.ClaimError as exc:
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
            resolved = queue_github.resolve_github_repo(
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
        claim_contract.ClaimError,
        queue_contract.QueueError,
        queue_selection.RoadmapError,
        privacy.PrivacyError,
    ) as exc:
        print(str(exc), file=stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(run_cli())
