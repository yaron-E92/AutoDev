from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Protocol

from automation import run_manifest, workflow_stages


DEFAULT_RUNTIME = "opencode"
RUNTIME_ENV = "AUTODEV_ROLE_RUNTIME"
USER_CONFIG_ENV = "AUTODEV_USER_CONFIG"
CONFIG_RELATIVE = Path(".autodev") / "config.json"
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
        }


class RoleRuntime(Protocol):
    name: str

    def role_snapshots(
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


def default_registry() -> dict[str, RuntimeFactory]:
    from automation.opencode_role_runtime import OpenCodeRoleRuntime

    return {DEFAULT_RUNTIME: OpenCodeRoleRuntime}


def resolve_runtime_name(repo: Path, requested: str = "") -> tuple[str, str]:
    repo = repo.expanduser().resolve()
    explicit = str(requested or "").strip()
    if explicit:
        return _validate_name(explicit), "explicit"

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


def user_config_path() -> Path | None:
    explicit = os.environ.get(USER_CONFIG_ENV, "").strip()
    if explicit:
        return Path(explicit).expanduser()
    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
    if xdg:
        return Path(xdg).expanduser() / "autodev" / "config.json"
    appdata = os.environ.get("APPDATA", "").strip()
    if os.name == "nt" and appdata:
        return Path(appdata).expanduser() / "AutoDev" / "config.json"
    try:
        home = Path.home()
    except RuntimeError:
        # Headless Windows/service accounts can legitimately have no resolvable
        # home directory. That means there is no implicit user config; it must
        # not prevent repository/default runtime selection.
        return None
    return home / ".config" / "autodev" / "config.json"


def select_runtime(
    repo: Path,
    *,
    requested: str = "",
    registry: Mapping[str, RuntimeFactory] | None = None,
) -> tuple[RoleRuntime, str]:
    name, source = resolve_runtime_name(repo, requested)
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
    persist_selection(repo, name=name, source=source)
    return runtime, source


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
