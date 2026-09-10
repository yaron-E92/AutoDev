from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


SCHEMA = "autodev.role-runtime.image-input-capability/v1"
EVIDENCE_FILE = "ux-multimodal-capability.json"
MANIFEST_KEY = "ux_multimodal_capability"
STATE_SUPPORTED = "supported"
STATE_UNSUPPORTED = "unsupported"
STATE_UNKNOWN = "unknown"
STATES = {STATE_SUPPORTED, STATE_UNSUPPORTED, STATE_UNKNOWN}


class RoleRuntimeCapabilityError(RuntimeError):
    pass


@dataclass(frozen=True)
class ImageInputCapability:
    state: str
    runtime: str
    provider: str
    model: str
    source: str
    detail: str = ""
    metadata_sha256: str = ""

    def __post_init__(self) -> None:
        if self.state not in STATES:
            raise RoleRuntimeCapabilityError(
                f"image-input capability state must be one of {sorted(STATES)}"
            )
        if not self.runtime.strip():
            raise RoleRuntimeCapabilityError("image-input capability runtime must be non-empty")
        if not self.source.strip():
            raise RoleRuntimeCapabilityError("image-input capability source must be non-empty")
        if self.model and not self.provider:
            raise RoleRuntimeCapabilityError(
                "image-input capability model identity requires a provider"
            )

    @property
    def route(self) -> str:
        if not self.provider or not self.model:
            return ""
        return f"{self.provider}/{self.model}"

    @property
    def fingerprint(self) -> str:
        payload = {
            "schema": SCHEMA,
            "state": self.state,
            "runtime": self.runtime,
            "provider": self.provider,
            "model": self.model,
            "source": self.source,
            "metadata_sha256": self.metadata_sha256,
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
            "schema": SCHEMA,
            "state": self.state,
            "runtime": self.runtime,
            "provider": self.provider,
            "model": self.model,
            "route": self.route,
            "source": self.source,
            "detail": self.detail[:4000],
            "metadata_sha256": self.metadata_sha256,
            "fingerprint": self.fingerprint,
        }


CapabilityResolver = Callable[..., ImageInputCapability]
_RESOLVERS: dict[str, CapabilityResolver] = {}


def register_resolver(runtime_name: str, resolver: CapabilityResolver) -> None:
    name = str(runtime_name or "").strip()
    if not name:
        raise RoleRuntimeCapabilityError("runtime capability resolver name must be non-empty")
    existing = _RESOLVERS.get(name)
    if existing is not None and existing is not resolver:
        raise RoleRuntimeCapabilityError(
            f"image-input capability resolver already registered for runtime {name!r}"
        )
    _RESOLVERS[name] = resolver


def image_input_capability(
    runtime: object,
    repo: Path,
    *,
    role: str,
    runner,
    which=None,
) -> ImageInputCapability:
    runtime_name = str(getattr(runtime, "name", "") or "").strip() or "unknown-runtime"
    method = getattr(runtime, "image_input_capability", None)
    if callable(method):
        try:
            value = method(
                repo,
                role=role,
                runner=runner,
                which=which,
            )
        except RoleRuntimeCapabilityError:
            raise
        except Exception as exc:
            return ImageInputCapability(
                state=STATE_UNKNOWN,
                runtime=runtime_name,
                provider="",
                model="",
                source="runtime capability hook",
                detail=f"runtime capability hook failed: {exc}",
            )
        return _validated(value, runtime_name)

    resolver = _RESOLVERS.get(runtime_name)
    if resolver is None:
        return ImageInputCapability(
            state=STATE_UNKNOWN,
            runtime=runtime_name,
            provider="",
            model="",
            source="runtime capability registry",
            detail="runtime does not expose authoritative image-input capability metadata",
        )
    try:
        value = resolver(
            runtime,
            repo,
            role=role,
            runner=runner,
            which=which,
        )
    except RoleRuntimeCapabilityError:
        raise
    except Exception as exc:
        return ImageInputCapability(
            state=STATE_UNKNOWN,
            runtime=runtime_name,
            provider="",
            model="",
            source="runtime capability registry",
            detail=f"registered capability resolver failed: {exc}",
        )
    return _validated(value, runtime_name)


def persist(repo: Path, capability: ImageInputCapability) -> Path:
    repo = repo.expanduser().resolve()
    run_root = repo / ".autodev-run"
    current = run_root / "current"
    path = current / EVIDENCE_FILE
    if not run_root.is_dir():
        return path
    current.mkdir(parents=True, exist_ok=True)
    payload = capability.to_json()
    _write_json_atomic(path, payload)

    manifest_path = current / "run-manifest.json"
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            manifest = None
        if isinstance(manifest, dict):
            manifest[MANIFEST_KEY] = dict(payload)
            _write_json_atomic(manifest_path, manifest)
    return path


def _validated(value: object, expected_runtime: str) -> ImageInputCapability:
    if not isinstance(value, ImageInputCapability):
        raise RoleRuntimeCapabilityError(
            "runtime image-input capability hook must return ImageInputCapability"
        )
    if value.runtime != expected_runtime:
        raise RoleRuntimeCapabilityError(
            "runtime image-input capability evidence changed the effective runtime identity"
        )
    return value


def _write_json_atomic(path: Path, value: dict[str, object]) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
