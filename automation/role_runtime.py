from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Protocol

from automation import role_output_contract, run_manifest, user_config, workflow_stages


DEFAULT_RUNTIME = "opencode"
RUNTIME_ENV = "AUTODEV_ROLE_RUNTIME"
SCHEDULER_RUNTIME_REQUEST_ENV = "AUTODEV_SCHEDULER_RUNTIME_REQUEST"
USER_CONFIG_ENV = user_config.USER_CONFIG_ENV
CONFIG_RELATIVE = Path(".autodev") / "config.json"
SCHEDULER_RUNTIME_STATE_RELATIVE = Path(".git") / "autodev" / "scheduler-runtime.json"
_RUNTIME_NAME = re.compile(r"^[a-z][a-z0-9_-]*$")


class RoleRuntimeError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        classification: str = workflow_stages.FAILURE_DETERMINISTIC,
    ) -> None:
        super().__init__(message)
        self.classification = classification


@dataclass(frozen=True)
class RoleInvocationContext:
    repo: Path
    role: str
    prompt: str
    phase: str = "work"
    repair_kind: str = ""
    timeout_seconds: int = 900
    output_contract: role_output_contract.RoleOutputContract | None = None
    ux_context_fingerprint: str = ""


@dataclass(frozen=True)
class RoleInvocationResult:
    runtime: str
    role: str
    phase: str
    returncode: int | None
    elapsed_ms: int
    stdout: str = ""
    stderr: str = ""
    termination: str = "completed"
    model: str = ""
    structured_output_mode: str = "fallback-text"
    structured_output_state: str = ""
    contract_name: str = ""
    contract_version: int = 0
    schema_retry_count: int = 0

    def as_dict(self) -> dict[str, object]:
        return {
            "runtime": self.runtime,
            "role": self.role,
            "phase": self.phase,
            "returncode": self.returncode,
            "elapsed_ms": self.elapsed_ms,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "termination": self.termination,
            "model": self.model,
            "structured_output_mode": self.structured_output_mode,
            "structured_output_state": self.structured_output_state,
            "contract_name": self.contract_name,
            "contract_version": self.contract_version,
            "schema_retry_count": self.schema_retry_count,
        }


@dataclass(frozen=True)
class SchedulerRuntimeState:
    """Safe, persisted scheduler/runtime execution identity.

    Runtime adapters own the contents of ``identity`` and ``environment``. They
    must never place credentials or other secrets here: the state is deliberately
    inspectable and is copied into the scheduler registration. ``environment`` is
    for reproducibility inputs such as PATH or non-secret generated provider
    overlays, not ambient shell capture.
    """

    name: str
    source: str
    identity: dict[str, object]
    environment: dict[str, str]
    routes: dict[str, str]

    @property
    def fingerprint(self) -> str:
        payload = {
            "name": self.name,
            "identity": self.identity,
            "routes": self.routes,
        }
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def to_json(self) -> dict[str, object]:
        return {
            "name": self.name,
            "source": self.source,
            "identity": dict(self.identity),
            "environment": dict(self.environment),
            "routes": dict(self.routes),
            "fingerprint": self.fingerprint,
        }

    @classmethod
    def from_json(cls, value: object) -> "SchedulerRuntimeState":
        if not isinstance(value, dict):
            raise RoleRuntimeError("scheduler runtime state must be a JSON object")
        name = _validate_name(str(value.get("name", "") or ""))
        source = str(value.get("source", "") or "registered")
        identity = value.get("identity", {})
        environment = value.get("environment", {})
        routes = value.get("routes", {})
        if not isinstance(identity, dict):
            raise RoleRuntimeError("scheduler runtime identity must be a JSON object")
        if not isinstance(environment, dict) or not all(
            isinstance(key, str) and isinstance(item, str)
            for key, item in environment.items()
        ):
            raise RoleRuntimeError("scheduler runtime environment must contain string values")
        if not isinstance(routes, dict) or not all(
            isinstance(key, str) and isinstance(item, str)
            for key, item in routes.items()
        ):
            raise RoleRuntimeError("scheduler runtime routes must contain string values")
        state = cls(
            name=name,
            source=source,
            identity=dict(identity),
            environment=dict(environment),
            routes=dict(routes),
        )
        recorded = str(value.get("fingerprint", "") or "")
        if recorded and recorded != state.fingerprint:
            raise RoleRuntimeError("scheduler runtime state fingerprint does not match its contents")
        return state


class RoleRuntime(Protocol):
    name: str

    def role_snapshots(
        self,
        repo: Path,
        *,
        runner: Callable[..., object],
        which=None,
    ) -> dict[str, object]: ...

    def privacy_evidence(
        self,
        repo: Path,
        *,
        runner: Callable[..., object],
        which=None,
    ) -> dict[str, object]: ...

    def invoke(
        self,
        context: RoleInvocationContext,
        *,
        runner: Callable[..., object],
        which=None,
    ) -> RoleInvocationResult: ...


RuntimeFactory = Callable[[], RoleRuntime]


def structured_output_capability(
    runtime: RoleRuntime,
    context: RoleInvocationContext,
    *,
    runner: Callable[..., object],
    which=None,
) -> str:
    """Resolve a runtime-owned capability without hard-coding provider checks in roles."""

    if context.output_contract is None:
        return role_output_contract.CAPABILITY_UNSUPPORTED
    method = getattr(runtime, "structured_output_capability", None)
    if not callable(method):
        return role_output_contract.CAPABILITY_UNSUPPORTED
    try:
        value = method(context, runner=runner, which=which)
    except RoleRuntimeError:
        raise
    except Exception as exc:
        raise RoleRuntimeError(
            f"could not resolve structured-output capability for runtime {runtime.name}: {exc}"
        ) from exc
    try:
        return role_output_contract.validate_capability(str(value or ""))
    except role_output_contract.RoleOutputContractError as exc:
        raise RoleRuntimeError(str(exc)) from exc


def provision_scheduler_worker(
    runtime: RoleRuntime,
    repo: Path,
    *,
    runner: Callable[..., object],
    which=None,
) -> None:
    method = getattr(runtime, "provision_scheduler_worker", None)
    if callable(method):
        method(
            repo.expanduser().resolve(),
            runner=runner,
            which=which,
        )


def validate_scheduler_worker(
    runtime: RoleRuntime,
    repo: Path,
    *,
    runner: Callable[..., object],
    which=None,
) -> None:
    method = getattr(runtime, "validate_scheduler_worker", None)
    if callable(method):
        method(
            repo.expanduser().resolve(),
            runner=runner,
            which=which,
        )


def validate_scheduler_routes(
    runtime: RoleRuntime,
    repo: Path,
    *,
    runner: Callable[..., object],
    which=None,
) -> None:
    method = getattr(runtime, "validate_scheduler_routes", None)
    if callable(method):
        method(
            repo.expanduser().resolve(),
            runner=runner,
            which=which,
        )


def prepare_scheduler_worker(
    runtime: RoleRuntime,
    repo: Path,
    *,
    source: str = "registered",
    runner: Callable[..., object],
    which=None,
) -> SchedulerRuntimeState:
    """Run the provider-neutral scheduler lifecycle for one selected runtime."""

    repo = repo.expanduser().resolve()
    provision_scheduler_worker(runtime, repo, runner=runner, which=which)
    validate_scheduler_worker(runtime, repo, runner=runner, which=which)
    validate_scheduler_routes(runtime, repo, runner=runner, which=which)

    environment_method = getattr(runtime, "scheduler_runtime_environment", None)
    environment_value = (
        environment_method(repo, runner=runner, which=which)
        if callable(environment_method)
        else {}
    )
    if not isinstance(environment_value, Mapping) or not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in environment_value.items()
    ):
        raise RoleRuntimeError(
            f"scheduler runtime {runtime.name} returned an invalid environment contract"
        )

    identity_method = getattr(runtime, "scheduler_runtime_identity", None)
    identity_value = (
        identity_method(repo, runner=runner, which=which)
        if callable(identity_method)
        else {"runtime": runtime.name}
    )
    if not isinstance(identity_value, Mapping):
        raise RoleRuntimeError(
            f"scheduler runtime {runtime.name} returned an invalid identity contract"
        )

    routes_method = getattr(runtime, "scheduler_runtime_routes", None)
    routes_value = (
        routes_method(repo, runner=runner, which=which)
        if callable(routes_method)
        else {}
    )
    if not isinstance(routes_value, Mapping) or not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in routes_value.items()
    ):
        raise RoleRuntimeError(
            f"scheduler runtime {runtime.name} returned an invalid route contract"
        )

    return SchedulerRuntimeState(
        name=_validate_name(str(runtime.name)),
        source=str(source or "registered"),
        identity=dict(identity_value),
        environment=dict(environment_value),
        routes=dict(routes_value),
    )


def scheduler_runtime_state(repo: Path) -> SchedulerRuntimeState | None:
    path = repo.expanduser().resolve() / SCHEDULER_RUNTIME_STATE_RELATIVE
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RoleRuntimeError(f"cannot read scheduler runtime state {path}: {exc}") from exc
    return SchedulerRuntimeState.from_json(raw)


def write_scheduler_runtime_state(repo: Path, state: SchedulerRuntimeState) -> None:
    path = repo.expanduser().resolve() / SCHEDULER_RUNTIME_STATE_RELATIVE
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.write_text(
            json.dumps(state.to_json(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise RoleRuntimeError(f"cannot persist scheduler runtime state {path}: {exc}") from exc


def clear_scheduler_runtime_state(repo: Path) -> None:
    path = repo.expanduser().resolve() / SCHEDULER_RUNTIME_STATE_RELATIVE
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        raise RoleRuntimeError(f"cannot clear scheduler runtime state {path}: {exc}") from exc


def scheduler_runtime_environment(repo: Path) -> dict[str, str]:
    state = scheduler_runtime_state(repo)
    return dict(state.environment) if state is not None else {}


def apply_scheduler_runtime_environment(repo: Path) -> dict[str, str]:
    """Apply the persisted non-secret runtime environment in this scheduler process."""

    environment = scheduler_runtime_environment(repo)
    for key, value in environment.items():
        os.environ[key] = value
    return environment


def refresh_scheduler_worker(
    repo: Path,
    *,
    requested: str = "",
    source: str = "registered",
    registry: Mapping[str, RuntimeFactory] | None = None,
    runner: Callable[..., object],
    which=None,
) -> SchedulerRuntimeState:
    repo = repo.expanduser().resolve()
    existing = scheduler_runtime_state(repo)
    if existing is not None:
        apply_scheduler_runtime_environment(repo)
    runtime_name = requested.strip() or (existing.name if existing is not None else "")
    runtime, resolved_source = select_scheduler_runtime(
        repo,
        requested=runtime_name,
        registry=registry,
    )
    state = prepare_scheduler_worker(
        runtime,
        repo,
        source=source or resolved_source,
        runner=runner,
        which=which,
    )
    write_scheduler_runtime_state(repo, state)
    apply_scheduler_runtime_environment(repo)
    return state


def default_registry() -> dict[str, RuntimeFactory]:
    from automation.opencode_role_runtime import OpenCodeRoleRuntime

    return {DEFAULT_RUNTIME: OpenCodeRoleRuntime}


def resolve_runtime_name(repo: Path, requested: str = "") -> tuple[str, str]:
    repo = repo.expanduser().resolve()
    explicit = str(requested or "").strip()
    if explicit:
        return _validate_name(explicit), "explicit"

    scheduler_state = scheduler_runtime_state(repo)
    if scheduler_state is not None:
        return scheduler_state.name, "scheduler-registration"

    env_value = os.environ.get(RUNTIME_ENV, "").strip()
    if env_value:
        return _validate_name(env_value), f"environment:{RUNTIME_ENV}"

    configured = _runtime_from_config(repo / CONFIG_RELATIVE, required=False)
    if configured:
        return _validate_name(configured), CONFIG_RELATIVE.as_posix()

    user_path = user_config_path()
    if user_path is not None:
        configured = _runtime_from_config(user_path, required=False)
        if configured:
            return _validate_name(configured), str(user_path)

    return DEFAULT_RUNTIME, "default"


def resolve_scheduler_runtime_name(repo: Path, requested: str = "") -> tuple[str, str]:
    """Resolve install-time scheduler runtime without trusting ambient shell overrides."""

    repo = repo.expanduser().resolve()
    explicit = str(requested or "").strip()
    if explicit:
        return _validate_name(explicit), "explicit-scheduler"

    configured = _runtime_from_config(repo / CONFIG_RELATIVE, required=False)
    if configured:
        return _validate_name(configured), CONFIG_RELATIVE.as_posix()

    user_path = user_config_path()
    if user_path is not None:
        configured = _runtime_from_config(user_path, required=False)
        if configured:
            return _validate_name(configured), str(user_path)

    return DEFAULT_RUNTIME, "default"


def user_config_path() -> Path | None:
    return user_config.config_path()


def _runtime_from_registry(
    name: str,
    *,
    registry: Mapping[str, RuntimeFactory] | None,
) -> RoleRuntime:
    factories = dict(registry or default_registry())
    factory = factories.get(name)
    if factory is None:
        available = ", ".join(sorted(factories)) or "(none)"
        raise RoleRuntimeError(
            f"unknown AutoDev role runtime {name!r}; registered runtimes: {available}"
        )
    runtime = factory()
    actual = _validate_name(str(getattr(runtime, "name", "") or ""))
    if actual != name:
        raise RoleRuntimeError(
            f"role runtime registry entry {name!r} produced runtime {actual!r}"
        )
    return runtime


def select_runtime(
    repo: Path,
    *,
    requested: str = "",
    registry: Mapping[str, RuntimeFactory] | None = None,
) -> tuple[RoleRuntime, str]:
    name, source = resolve_runtime_name(repo, requested)
    runtime = _runtime_from_registry(name, registry=registry)
    persist_selection(repo, name=name, source=source)
    return runtime, source


def select_scheduler_runtime(
    repo: Path,
    *,
    requested: str = "",
    registry: Mapping[str, RuntimeFactory] | None = None,
) -> tuple[RoleRuntime, str]:
    name, source = resolve_scheduler_runtime_name(repo, requested)
    return _runtime_from_registry(name, registry=registry), source


def build_role_snapshot(
    *,
    runtime: str,
    role: str,
    configured: dict[str, object] | None = None,
    safe_metadata: dict[str, object] | None = None,
) -> dict[str, object]:
    configured_value = {"runtime": runtime, "role": role, **dict(configured or {})}
    safe = {"runtime": runtime, "role": role, **dict(safe_metadata or {})}
    return run_manifest.build_role_snapshot(configured_value, safe)


def persist_selection(
    repo: Path,
    *,
    name: str,
    source: str,
    force_manifest: bool = False,
) -> None:
    """Persist safe runtime selection without erasing an unvalidated resume identity.

    Diagnostics may show the runtime selected for the current invocation immediately.
    A pre-existing manifest keeps its previous runtime identity until snapshot
    reconciliation succeeds, unless the caller explicitly confirms that transition.
    """

    repo = repo.expanduser().resolve()
    current = repo / workflow_stages.CURRENT_DIR
    diagnostics_path = current / workflow_stages.DIAGNOSTICS_FILE
    if current.is_dir():
        diagnostics = _read_json(diagnostics_path)
        diagnostics["role_runtime"] = {"name": name, "source": source}
        _write_json_atomic(diagnostics_path, diagnostics)

    manifest_path = current / run_manifest.MANIFEST_NAME
    if not manifest_path.is_file():
        return
    try:
        manifest = run_manifest.load_manifest(manifest_path)
    except (OSError, ValueError, run_manifest.ManifestError):
        return
    previous = manifest.get("role_runtime", {})
    previous_name = str(previous.get("name", "")) if isinstance(previous, dict) else ""
    if previous_name and previous_name != name and not force_manifest:
        return
    manifest["role_runtime"] = {"name": name, "source": source}
    run_manifest.save_manifest(manifest_path, manifest)


def selected_runtime_from_manifest(repo: Path) -> str:
    path = (
        repo.expanduser().resolve()
        / workflow_stages.CURRENT_DIR
        / run_manifest.MANIFEST_NAME
    )
    if not path.is_file():
        return ""
    try:
        manifest = run_manifest.load_manifest(path)
    except (OSError, ValueError, run_manifest.ManifestError):
        return ""
    value = manifest.get("role_runtime", {})
    return str(value.get("name", "")) if isinstance(value, dict) else ""


def _runtime_from_config(path: Path, *, required: bool) -> str:
    if not path.is_file():
        if required:
            raise RoleRuntimeError(f"runtime configuration does not exist: {path}")
        return ""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RoleRuntimeError(f"cannot read runtime configuration {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise RoleRuntimeError(f"runtime configuration {path} must contain a JSON object")
    raw = value.get("role_runtime", "")
    if raw in (None, ""):
        return ""
    if not isinstance(raw, str):
        raise RoleRuntimeError(
            f"runtime configuration {path} field role_runtime must be a string"
        )
    return raw.strip()


def _validate_name(value: str) -> str:
    name = value.strip().casefold()
    if not name or not _RUNTIME_NAME.fullmatch(name):
        raise RoleRuntimeError(
            "role runtime names must start with a letter and contain only lowercase letters, digits, '-' or '_'"
        )
    return name


def _read_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_json_atomic(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
