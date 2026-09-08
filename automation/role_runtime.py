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
    # Once a logical role invocation has crossed from native Structured Output to
    # compatibility text, its one AutoDev protocol correction must stay on that
    # text path. This prevents native -> fallback -> native retry loops while still
    # allowing a native correction when the initial attempt itself stayed native.
    fallback_text_only: bool = False


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
    identity_method = getattr(runtime, "scheduler_runtime_identity", None)
    routes_method = getattr(runtime, "scheduler_runtime_routes", None)

    environment = (
        environment_method(repo, runner=runner, which=which)
        if callable(environment_method)
        else {}
    )
    identity = (
        identity_method(repo, runner=runner, which=which)
        if callable(identity_method)
        else {"runtime": runtime.name}
    )
    routes = (
        routes_method(repo, runner=runner, which=which)
        if callable(routes_method)
        else {}
    )
    if not isinstance(environment, Mapping) or not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in environment.items()
    ):
        raise RoleRuntimeError(
            f"scheduler runtime {runtime.name} returned an invalid environment contract"
        )
    if not isinstance(identity, Mapping):
        raise RoleRuntimeError(
            f"scheduler runtime {runtime.name} returned an invalid identity contract"
        )
    if not isinstance(routes, Mapping) or not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in routes.items()
    ):
        raise RoleRuntimeError(
            f"scheduler runtime {runtime.name} returned an invalid route contract"
        )
    return SchedulerRuntimeState(
        name=runtime.name,
        source=source,
        identity=dict(identity),
        environment=dict(environment),
        routes=dict(routes),
    )


def scheduler_runtime_state(repo: Path) -> SchedulerRuntimeState | None:
    path = repo.expanduser().resolve() / SCHEDULER_RUNTIME_STATE_RELATIVE
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RoleRuntimeError(f"cannot read scheduler runtime state {path}: {exc}") from exc
    return SchedulerRuntimeState.from_json(value)


def write_scheduler_runtime_state(repo: Path, state: SchedulerRuntimeState) -> Path:
    repo = repo.expanduser().resolve()
    path = repo / SCHEDULER_RUNTIME_STATE_RELATIVE
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(state.to_json(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
    return path


def clear_scheduler_runtime_state(repo: Path) -> None:
    (repo.expanduser().resolve() / SCHEDULER_RUNTIME_STATE_RELATIVE).unlink(missing_ok=True)


def apply_scheduler_runtime_environment(repo: Path) -> SchedulerRuntimeState | None:
    state = scheduler_runtime_state(repo)
    if state is None:
        return None
    for key, value in state.environment.items():
        os.environ[key] = value
    return state


def resolve_runtime_name(repo: Path, *, requested: str = "") -> tuple[str, str]:
    """Resolve the role runtime for interactive/manual role execution."""

    if requested:
        return _validate_name(requested), "explicit"
    worker_state = scheduler_runtime_state(repo)
    if worker_state is not None:
        return worker_state.name, "scheduler-worker"
    environment = os.environ.get(RUNTIME_ENV, "").strip()
    if environment:
        return _validate_name(environment), "environment"
    repo_value = _load_config(repo.expanduser().resolve() / CONFIG_RELATIVE).get("role_runtime")
    if repo_value:
        return _validate_name(str(repo_value)), "repository"
    user_value = _load_config(user_config.user_config_path()).get("role_runtime")
    if user_value:
        return _validate_name(str(user_value)), "user"
    return DEFAULT_RUNTIME, "builtin"


def resolve_scheduler_runtime_name(repo: Path, *, requested: str = "") -> tuple[str, str]:
    """Resolve unattended scheduler runtime without inheriting transient shell state."""

    if requested:
        return _validate_name(requested), "explicit"
    repo_value = _load_config(repo.expanduser().resolve() / CONFIG_RELATIVE).get("role_runtime")
    if repo_value:
        return _validate_name(str(repo_value)), "repository"
    user_value = _load_config(user_config.user_config_path()).get("role_runtime")
    if user_value:
        return _validate_name(str(user_value)), "user"
    return DEFAULT_RUNTIME, "builtin"


def default_registry() -> dict[str, RuntimeFactory]:
    from automation.opencode_scheduler_runtime import OpenCodeSchedulerRoleRuntime

    return {"opencode": OpenCodeSchedulerRoleRuntime}


def _runtime_from_registry(
    name: str,
    *,
    registry: Mapping[str, RuntimeFactory] | None = None,
) -> RoleRuntime:
    effective = registry or default_registry()
    factory = effective.get(name)
    if factory is None:
        raise RoleRuntimeError(
            f"unknown role runtime {name!r}; available runtimes: "
            + ", ".join(sorted(effective))
        )
    return factory()


def select_runtime(
    repo: Path,
    *,
    requested: str = "",
    registry: Mapping[str, RuntimeFactory] | None = None,
) -> tuple[RoleRuntime, str]:
    name, source = resolve_runtime_name(repo, requested=requested)
    return _runtime_from_registry(name, registry=registry), source


def select_scheduler_runtime(
    repo: Path,
    *,
    requested: str = "",
    registry: Mapping[str, RuntimeFactory] | None = None,
) -> tuple[RoleRuntime, str]:
    name, source = resolve_scheduler_runtime_name(repo, requested=requested)
    return _runtime_from_registry(name, registry=registry), source


def refresh_scheduler_worker(
    repo: Path,
    *,
    requested: str = "",
    runner: Callable[..., object],
    which=None,
    registry: Mapping[str, RuntimeFactory] | None = None,
) -> SchedulerRuntimeState:
    repo = repo.expanduser().resolve()
    previous = scheduler_runtime_state(repo)
    effective_requested = requested or (previous.name if previous is not None else "")
    if previous is not None:
        for key, value in previous.environment.items():
            os.environ[key] = value
    runtime, source = select_scheduler_runtime(
        repo,
        requested=effective_requested,
        registry=registry,
    )
    state = prepare_scheduler_worker(
        runtime,
        repo,
        source=source,
        runner=runner,
        which=which,
    )
    write_scheduler_runtime_state(repo, state)
    for key, value in state.environment.items():
        os.environ[key] = value
    return state


def _validate_name(value: str) -> str:
    name = str(value or "").strip().casefold()
    if not _RUNTIME_NAME.fullmatch(name):
        raise RoleRuntimeError(
            f"invalid role runtime name {value!r}; expected {_RUNTIME_NAME.pattern}"
        )
    return name


def _load_config(path: Path) -> dict[str, object]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}
