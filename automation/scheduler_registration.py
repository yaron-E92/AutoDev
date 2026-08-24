from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Callable

from automation import distributed_claims, issue_queue, privacy, queue_selection, user_install
from automation.scheduler_backends import (
    _backend_state,
    _install_backend,
    _select_backend,
    _uninstall_backend,
)
from automation.scheduler_process import (
    _default_branch,
    _git_status,
    _origin_url,
    _require_ok,
    _run_command,
)
from automation.scheduler_types import (
    BACKEND_AUTO,
    DEFAULT_CADENCE_MINUTES,
    MAX_CADENCE_MINUTES,
    MIN_CADENCE_MINUTES,
    SCHEDULER_SCHEMA,
    SUPPORTED_BACKENDS,
    SchedulerError,
    SchedulerRegistration,
    SchedulerStatus,
    _now,
    _repo_parts,
    _repo_root,
    _task_id,
    registration_path,
    worker_path,
)

_REQUIRED_POLICY = (
    Path(".autodev") / "repo.json",
    issue_queue.QUEUE_CONFIG,
    privacy.PRIVACY_CONFIG,
)


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
        detail = ", ".join(
            [
                *(f"missing {item}" for item in missing),
                *(f"different {item}" for item in mismatched),
            ]
        )
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
