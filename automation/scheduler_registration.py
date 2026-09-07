from __future__ import annotations

from automation import (
    claim_identity,
    privacy_authorization,
    queue_contract,
    queue_github,
    queue_policy,
    role_runtime,
    user_config,
)

import json
import os
import shutil
import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator

from automation import privacy, queue_selection, user_install
from automation.scheduler_backends import (
    _backend_state,
    _install_backend,
    _select_backend,
    _uninstall_backend,
)
from automation.scheduler_process import (
    _default_branch,
    _git,
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
    SUPPORTED_SCHEDULER_SCHEMAS,
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
    queue_contract.QUEUE_CONFIG,
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
    policy = queue_policy.load_policy(repo)
    if not policy.autonomous_execution:
        raise SchedulerError(
            "repository queue policy disables autonomous_execution; enable it before installing a scheduler"
        )
    claim_identity.load_claim_policy(repo)
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
    which: Callable[[str], str | None] = shutil.which,
) -> tuple[Path, str]:
    worker = worker_path(github_repo, home=home)
    gh = which("gh")
    if not gh:
        raise SchedulerError(
            "GitHub CLI was not found on PATH; scheduler workers require non-interactive gh authentication"
        )
    auth_argv = [gh, "auth", "status", "--hostname", "github.com"]
    _require_ok(_run_command(auth_argv, runner=runner), auth_argv)
    origin = f"https://github.com/{github_repo}.git"
    credential_helper = "!" + subprocess.list2cmdline([gh, "auth", "git-credential"])
    if not worker.exists():
        worker.parent.mkdir(parents=True, exist_ok=True)
        argv = [
            "git",
            "-c",
            f"credential.https://github.com.helper={credential_helper}",
            "clone",
            "--origin",
            "origin",
            str(worker),
        ]
        completed = _run_command(argv, runner=runner)
        try:
            _require_ok(completed, argv)
        except SchedulerError:
            if worker.exists():
                shutil.rmtree(worker, ignore_errors=True)
            raise
    if not worker.is_dir() or not (worker / ".git").exists():
        raise SchedulerError(
            f"dedicated worker path exists but is not an AutoDev-managed Git clone: {worker}"
        )
    worker_origin = _origin_url(worker, runner=runner)
    worker_repo = user_config.github_repository_from_remote(worker_origin)
    if not worker_repo or worker_repo.casefold() != github_repo.casefold():
        raise SchedulerError(
            f"dedicated worker origin does not match source repository identity {github_repo}: "
            f"{worker_origin!r}; refusing to reuse {worker}"
        )
    _git(worker, ["remote", "set-url", "origin", origin], runner=runner)
    _git(
        worker,
        ["config", "credential.https://github.com.helper", credential_helper],
        runner=runner,
    )
    _validate_worker_policy(source, worker)
    return worker, _default_branch(worker, runner=runner)


def _validate_headless_worker_transport(
    worker: Path,
    *,
    runner: Callable[..., object],
) -> None:
    _git(worker, ["fetch", "--prune", "origin"], runner=runner)
    _git(
        worker,
        [
            "push",
            "--dry-run",
            "--porcelain",
            "origin",
            "HEAD:refs/heads/autodev/scheduler-auth-probe",
        ],
        runner=runner,
    )


def _validate_headless_model_policy(
    worker: Path,
    *,
    runner: Callable[..., object],
    which: Callable[[str], str | None],
    runtime=None,
) -> None:
    try:
        if runtime is None:
            runtime, _ = role_runtime.select_runtime(worker)
        evidence = runtime.privacy_evidence(
            worker,
            runner=runner,
            which=which,
        )
    except role_runtime.RoleRuntimeError as exc:
        raise SchedulerError(
            f"scheduler headless model/privacy preflight failed: {exc}; "
            "configure concrete role routes before installing the scheduler"
        ) from exc

    try:
        privacy_authorization.authorize_headless(
            worker,
            evidence.values(),
        )
    except privacy_authorization.PrivacyConsentRequired as exc:
        routes = "\n".join(
            f"  {item.role:<13} {item.route}" for item in exc.decisions
        )
        raise SchedulerError(
            "scheduler privacy preflight requires consent for:\n"
            + routes
            + "\nRun `autodev privacy consent` in the source repository "
            "(or choose a compliant model profile), then retry "
            "`autodev scheduler install`."
        ) from exc
    except privacy.PrivacyError as exc:
        raise SchedulerError(
            f"scheduler privacy preflight rejected the configured route: {exc}"
        ) from exc


def _load_registration(path: Path) -> SchedulerRegistration | None:
    path = path.expanduser().resolve()
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SchedulerError(f"invalid scheduler registration: {path}") from exc
    schema = raw.get("schema_version") if isinstance(raw, dict) else None
    if not isinstance(raw, dict) or schema not in SUPPORTED_SCHEDULER_SCHEMAS:
        raise SchedulerError(f"unsupported scheduler registration schema: {path}")
    github_repo = str(raw.get("github_repository", ""))
    _repo_parts(github_repo)
    backend = str(raw.get("backend", ""))
    if backend not in SUPPORTED_BACKENDS:
        raise SchedulerError(f"unsupported scheduler backend in {path}: {backend!r}")
    cadence = int(raw.get("cadence_minutes", 0) or 0)
    if not MIN_CADENCE_MINUTES <= cadence <= MAX_CADENCE_MINUTES:
        raise SchedulerError(f"invalid scheduler cadence in {path}: {cadence}")

    runtime_name = str(raw.get("role_runtime", "") or "").strip()
    runtime_state_raw = raw.get("runtime_state")
    runtime_state: dict[str, object] | None = None
    if runtime_state_raw is not None:
        try:
            parsed_state = role_runtime.SchedulerRuntimeState.from_json(runtime_state_raw)
        except role_runtime.RoleRuntimeError as exc:
            raise SchedulerError(f"invalid scheduler runtime state in {path}: {exc}") from exc
        if runtime_name and runtime_name != parsed_state.name:
            raise SchedulerError(
                f"scheduler registration runtime {runtime_name!r} does not match "
                f"runtime state {parsed_state.name!r}: {path}"
            )
        runtime_name = runtime_name or parsed_state.name
        runtime_state = parsed_state.to_json()

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
        role_runtime=runtime_name,
        runtime_state=runtime_state,
        last_run=dict(last_run) if isinstance(last_run, dict) else None,
    )


def _write_registration(path: Path, registration: SchedulerRegistration) -> None:
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = registration.to_json()

    # Older scheduler code reconstructs SchedulerRegistration when recording a
    # tick. Preserve the independently validated runtime identity until every call
    # site has naturally migrated to the schema-2 fields.
    if not registration.role_runtime and path.is_file():
        try:
            previous = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            previous = {}
        if isinstance(previous, dict):
            previous_runtime = previous.get("role_runtime")
            previous_state = previous.get("runtime_state")
            if isinstance(previous_runtime, str) and previous_runtime.strip():
                rendered["role_runtime"] = previous_runtime.strip()
            if isinstance(previous_state, dict):
                rendered["runtime_state"] = previous_state

    temp = path.with_suffix(path.suffix + ".tmp")
    try:
        temp.write_text(
            json.dumps(rendered, indent=2, sort_keys=True) + "\n",
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


@contextmanager
def _temporary_runtime_environment(environment: dict[str, str]) -> Iterator[None]:
    previous = {key: os.environ.get(key) for key in environment}
    for key, value in environment.items():
        os.environ[key] = value
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _prepare_runtime_registration(
    source: Path,
    worker: Path,
    *,
    requested_runtime: str,
    runner: Callable[..., object],
    which: Callable[[str], str | None],
) -> tuple[object, role_runtime.SchedulerRuntimeState]:
    try:
        runtime, source_label = role_runtime.select_scheduler_runtime(
            source,
            requested=requested_runtime,
        )
        state = role_runtime.prepare_scheduler_worker(
            runtime,
            worker,
            source=source_label,
            runner=runner,
            which=which,
        )
    except role_runtime.RoleRuntimeError as exc:
        raise SchedulerError(str(exc)) from exc

    if _git_status(worker, runner=runner):
        existing_run = queue_selection.inspect_existing_run(worker)
        if existing_run.state == "NONE":
            raise SchedulerError(
                f"dedicated worker contains unexpected local changes after runtime provisioning: "
                f"{worker}; AutoDev will not reset or delete them"
            )

    with _temporary_runtime_environment(state.environment):
        _validate_headless_model_policy(
            worker,
            runner=runner,
            which=which,
            runtime=runtime,
        )
    return runtime, state


def _restore_worker_runtime_state(
    worker: Path,
    previous: role_runtime.SchedulerRuntimeState | None,
) -> None:
    try:
        if previous is None:
            role_runtime.clear_scheduler_runtime_state(worker)
        else:
            role_runtime.write_scheduler_runtime_state(worker, previous)
    except role_runtime.RoleRuntimeError:
        # Preserve the original scheduler/backend exception. The registration is
        # still restored below and remains authoritative for the next repair run.
        pass


def install_scheduler(
    repo: Path,
    *,
    github_repo: str = "",
    backend: str = BACKEND_AUTO,
    cadence_minutes: int | None = None,
    launcher: str = "",
    runtime_name: str = "",
    home: Path | None = None,
    platform_name: str | None = None,
    runner: Callable[..., object] = subprocess.run,
    which: Callable[[str], str | None] = shutil.which,
) -> SchedulerRegistration:
    source = _repo_root(repo)
    _validate_source_policy(source)
    if cadence_minutes is None:
        try:
            cadence_minutes = user_config.scheduler_cadence()
        except user_config.UserConfigError as exc:
            raise SchedulerError(f"invalid AutoDev user scheduler configuration: {exc}") from exc
        if cadence_minutes is None:
            cadence_minutes = DEFAULT_CADENCE_MINUTES
    if not MIN_CADENCE_MINUTES <= cadence_minutes <= MAX_CADENCE_MINUTES:
        raise SchedulerError(
            f"cadence must be between {MIN_CADENCE_MINUTES} and {MAX_CADENCE_MINUTES} minutes"
        )
    resolved = queue_github.resolve_github_repo(
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
        which=which,
    )
    _validate_headless_worker_transport(worker, runner=runner)

    previous_worker_state = role_runtime.scheduler_runtime_state(worker)
    requested = str(runtime_name or os.environ.get(role_runtime.SCHEDULER_RUNTIME_REQUEST_ENV, "")).strip()
    if not requested and existing and existing.role_runtime:
        requested = existing.role_runtime
    _runtime, runtime_state = _prepare_runtime_registration(
        source,
        worker,
        requested_runtime=requested,
        runner=runner,
        which=which,
    )

    claim_identity.worker_identity(home=home)
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
        role_runtime=runtime_state.name,
        runtime_state=runtime_state.to_json(),
        last_run=existing.last_run if existing else None,
    )

    role_runtime.write_scheduler_runtime_state(worker, runtime_state)
    _write_registration(path, registration)
    backend_changed = bool(existing and existing.backend != selected_backend)
    try:
        if backend_changed and existing is not None:
            _uninstall_backend(existing, home=home, runner=runner)
        _install_backend(registration, path, home=home, runner=runner)
    except Exception:
        _restore_worker_runtime_state(worker, previous_worker_state)
        if existing:
            _write_registration(path, existing)
            try:
                _install_backend(existing, path, home=home, runner=runner)
            except Exception:
                pass
        else:
            path.unlink(missing_ok=True)
        raise
    return registration


def switch_scheduler_runtime(
    registration_file: Path,
    requested_runtime: str,
    *,
    home: Path | None = None,
    runner: Callable[..., object] = subprocess.run,
    which: Callable[[str], str | None] = shutil.which,
) -> SchedulerRegistration:
    path = registration_file.expanduser().resolve()
    existing = _load_registration(path)
    if existing is None:
        raise SchedulerError(f"scheduler is not installed: {path}")
    requested = str(requested_runtime or "").strip()
    if not requested:
        raise SchedulerError("scheduler runtime set requires a runtime name")

    source = _repo_root(Path(existing.source_repository))
    worker = Path(existing.worker_repository).expanduser().resolve()
    if not worker.is_dir() or not (worker / ".git").exists():
        raise SchedulerError(f"dedicated scheduler worker is missing or invalid: {worker}")
    _validate_source_policy(source)
    _validate_worker_policy(source, worker)
    _validate_headless_worker_transport(worker, runner=runner)

    previous_worker_state = role_runtime.scheduler_runtime_state(worker)
    _runtime, runtime_state = _prepare_runtime_registration(
        source,
        worker,
        requested_runtime=requested,
        runner=runner,
        which=which,
    )
    replacement = SchedulerRegistration(
        github_repository=existing.github_repository,
        source_repository=existing.source_repository,
        worker_repository=existing.worker_repository,
        default_branch=existing.default_branch,
        backend=existing.backend,
        cadence_minutes=existing.cadence_minutes,
        launcher=existing.launcher,
        task_id=existing.task_id,
        installed_at=existing.installed_at,
        role_runtime=runtime_state.name,
        runtime_state=runtime_state.to_json(),
        last_run=existing.last_run,
    )

    role_runtime.write_scheduler_runtime_state(worker, runtime_state)
    _write_registration(path, replacement)
    try:
        _install_backend(replacement, path, home=home, runner=runner)
    except Exception:
        _restore_worker_runtime_state(worker, previous_worker_state)
        _write_registration(path, existing)
        try:
            _install_backend(existing, path, home=home, runner=runner)
        except Exception:
            pass
        raise
    return replacement


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
            resolved = queue_github.resolve_github_repo(source, runner=runner)
        path = registration_path(resolved, home=home)
    registration = _load_registration(path)
    if registration is None:
        return SchedulerStatus(state="NOT_INSTALLED", github_repository=resolved)
    backend_state = _backend_state(registration, home=home, runner=runner)
    worker = Path(registration.worker_repository).expanduser()
    fingerprint = ""
    if isinstance(registration.runtime_state, dict):
        try:
            fingerprint = role_runtime.SchedulerRuntimeState.from_json(
                registration.runtime_state
            ).fingerprint
        except role_runtime.RoleRuntimeError:
            fingerprint = ""
    return SchedulerStatus(
        state="INSTALLED" if backend_state == "active" else "NEEDS_ATTENTION",
        github_repository=registration.github_repository,
        backend=registration.backend,
        backend_state=backend_state,
        source_repository=registration.source_repository,
        worker_repository=registration.worker_repository,
        worker_exists=worker.is_dir() and (worker / ".git").exists(),
        cadence_minutes=registration.cadence_minutes,
        role_runtime=registration.role_runtime,
        runtime_fingerprint=fingerprint,
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
            resolved = queue_github.resolve_github_repo(source, runner=runner)
        path = registration_path(resolved, home=home)
    registration = _load_registration(path)
    if registration is None:
        return SchedulerStatus(state="NOT_INSTALLED", github_repository=resolved)
    _uninstall_backend(registration, home=home, runner=runner)
    worker = Path(registration.worker_repository).expanduser().resolve()
    if worker.is_dir() and (worker / ".git").exists():
        try:
            role_runtime.clear_scheduler_runtime_state(worker)
        except role_runtime.RoleRuntimeError:
            pass
    path.unlink(missing_ok=True)
    fingerprint = ""
    if isinstance(registration.runtime_state, dict):
        try:
            fingerprint = role_runtime.SchedulerRuntimeState.from_json(
                registration.runtime_state
            ).fingerprint
        except role_runtime.RoleRuntimeError:
            pass
    return SchedulerStatus(
        state="UNINSTALLED",
        github_repository=registration.github_repository,
        backend=registration.backend,
        backend_state="removed",
        source_repository=registration.source_repository,
        worker_repository=registration.worker_repository,
        worker_exists=Path(registration.worker_repository).is_dir(),
        cadence_minutes=registration.cadence_minutes,
        role_runtime=registration.role_runtime,
        runtime_fingerprint=fingerprint,
        last_run=registration.last_run,
    )
