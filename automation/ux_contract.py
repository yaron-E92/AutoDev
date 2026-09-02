from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable


BUNDLE_SCHEMA = "autodev.ux.bundle/v1"
MANIFEST_NAME = "ux-manifest.json"
DEFAULT_MAX_FILES = 512
DEFAULT_MAX_TOTAL_BYTES = 128 * 1024 * 1024
DEFAULT_MAX_FILE_BYTES = 32 * 1024 * 1024


class UXBundleError(ValueError):
    """A deterministic validation failure for a UX artifact bundle."""


@dataclass(frozen=True)
class UXBundleManifest:
    schema: str
    product: str
    contract: str
    prototype: str = ""
    journeys: str = ""
    principles: str = ""
    annexes: tuple[str, ...] = ()
    references_root: str = ""
    shared_artifact: str = ""
    screens: dict[str, str] | None = None
    states: dict[str, str] | None = None
    verifier: str = ""
    metadata: dict[str, str] | None = None
    figma: dict[str, str] | None = None

    def selected_paths(
        self,
        *,
        screen_ids: Iterable[str] = (),
        state_ids: Iterable[str] = (),
        include_journeys: bool = False,
    ) -> tuple[str, ...]:
        """Return a bounded, explicit subset for downstream role/context generation."""
        selected = {self.contract}
        if self.principles:
            selected.add(self.principles)
        selected.update(self.annexes)
        if include_journeys and self.journeys:
            selected.add(self.journeys)
        for key in screen_ids:
            if self.screens and key in self.screens:
                selected.add(self.screens[key])
        for key in state_ids:
            if self.states and key in self.states:
                selected.add(self.states[key])
        return tuple(sorted(path for path in selected if path))


def _safe_relative_path(value: object, *, field: str, allow_empty: bool = False) -> str:
    text = str(value or "").strip().replace("\\", "/")
    if not text:
        if allow_empty:
            return ""
        raise UXBundleError(f"{field} must be a non-empty bundle-relative path")
    if "\x00" in text:
        raise UXBundleError(f"{field} contains a NUL byte")
    path = PurePosixPath(text)
    drive_like = bool(path.parts and ":" in path.parts[0])
    if path.is_absolute() or drive_like or ".." in path.parts or "." in path.parts:
        raise UXBundleError(f"{field} must not escape the UX bundle root: {text!r}")
    if any(part in {"", "~"} for part in path.parts):
        raise UXBundleError(f"{field} contains an unsafe path component: {text!r}")
    if len(text) > 512:
        raise UXBundleError(f"{field} path is too long")
    return path.as_posix()


def _path_list(value: object, *, field: str) -> tuple[str, ...]:
    if value in (None, ""):
        return ()
    if not isinstance(value, list):
        raise UXBundleError(f"{field} must be a JSON array")
    if len(value) > 128:
        raise UXBundleError(f"{field} contains too many entries")
    return tuple(_safe_relative_path(item, field=f"{field}[]") for item in value)


def _path_map(value: object, *, field: str) -> dict[str, str]:
    if value in (None, ""):
        return {}
    if not isinstance(value, dict):
        raise UXBundleError(f"{field} must be a JSON object")
    if len(value) > 256:
        raise UXBundleError(f"{field} contains too many entries")
    result: dict[str, str] = {}
    for raw_key, raw_path in value.items():
        key = str(raw_key or "").strip()
        if not key or len(key) > 128:
            raise UXBundleError(f"{field} contains an invalid identifier")
        result[key] = _safe_relative_path(raw_path, field=f"{field}.{key}")
    return result


def _string_map(value: object, *, field: str) -> dict[str, str]:
    if value in (None, ""):
        return {}
    if not isinstance(value, dict):
        raise UXBundleError(f"{field} must be a JSON object")
    if len(value) > 64:
        raise UXBundleError(f"{field} contains too many entries")
    result: dict[str, str] = {}
    for raw_key, raw_value in value.items():
        key = str(raw_key or "").strip()
        item = str(raw_value or "").strip()
        if not key or len(key) > 128:
            raise UXBundleError(f"{field} contains an invalid key")
        if len(item) > 2048:
            raise UXBundleError(f"{field}.{key} is too long")
        result[key] = item
    return result


def parse_manifest(value: object) -> UXBundleManifest:
    if not isinstance(value, dict):
        raise UXBundleError("UX bundle manifest must be a JSON object")
    schema = str(value.get("schema", "")).strip()
    if schema != BUNDLE_SCHEMA:
        raise UXBundleError(
            f"unsupported UX bundle schema {schema!r}; expected {BUNDLE_SCHEMA!r}"
        )
    product = str(value.get("product", "")).strip()
    if not product or len(product) > 128:
        raise UXBundleError("UX bundle product must be a non-empty identifier")
    shared = value.get("shared", {})
    if shared in (None, ""):
        shared = {}
    if not isinstance(shared, dict):
        raise UXBundleError("shared must be a JSON object")
    references = value.get("references", {})
    if references in (None, ""):
        references = {}
    if not isinstance(references, dict):
        raise UXBundleError("references must be a JSON object")
    return UXBundleManifest(
        schema=schema,
        product=product,
        contract=_safe_relative_path(value.get("contract"), field="contract"),
        prototype=_safe_relative_path(value.get("prototype"), field="prototype", allow_empty=True),
        journeys=_safe_relative_path(value.get("journeys"), field="journeys", allow_empty=True),
        principles=_safe_relative_path(value.get("principles"), field="principles", allow_empty=True),
        annexes=_path_list(value.get("annexes"), field="annexes"),
        references_root=_safe_relative_path(
            references.get("root"), field="references.root", allow_empty=True
        ),
        shared_artifact=str(shared.get("artifact", "") or "").strip(),
        screens=_path_map(value.get("screens"), field="screens"),
        states=_path_map(value.get("states"), field="states"),
        verifier=_safe_relative_path(value.get("verifier"), field="verifier", allow_empty=True),
        metadata=_string_map(value.get("metadata"), field="metadata"),
        figma=_string_map(value.get("figma"), field="figma"),
    )


def load_manifest(
    root: Path,
    *,
    max_files: int = DEFAULT_MAX_FILES,
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
) -> UXBundleManifest:
    root = root.expanduser().resolve()
    path = root / MANIFEST_NAME
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise UXBundleError(f"UX bundle is missing {MANIFEST_NAME}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise UXBundleError(f"UX bundle manifest is unreadable or malformed: {path}") from exc
    manifest = parse_manifest(raw)
    validate_bundle_files(
        root,
        manifest,
        max_files=max_files,
        max_total_bytes=max_total_bytes,
        max_file_bytes=max_file_bytes,
    )
    return manifest


def _required_manifest_paths(manifest: UXBundleManifest) -> set[str]:
    values = {
        manifest.contract,
        manifest.prototype,
        manifest.journeys,
        manifest.principles,
        manifest.verifier,
        *manifest.annexes,
        *(manifest.screens or {}).values(),
        *(manifest.states or {}).values(),
    }
    return {value for value in values if value}


def validate_bundle_files(
    root: Path,
    manifest: UXBundleManifest,
    *,
    max_files: int = DEFAULT_MAX_FILES,
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
) -> None:
    root = root.expanduser().resolve()
    if not root.is_dir():
        raise UXBundleError(f"resolved UX artifact root does not exist: {root}")
    files = [path for path in root.rglob("*") if path.is_file()]
    if len(files) > max_files:
        raise UXBundleError(f"UX bundle exceeds file-count limit ({max_files})")
    total = 0
    for path in files:
        try:
            resolved = path.resolve(strict=True)
            resolved.relative_to(root)
        except (OSError, ValueError) as exc:
            raise UXBundleError(f"UX bundle contains an unsafe path: {path}") from exc
        size = path.stat().st_size
        if size > max_file_bytes:
            raise UXBundleError(
                f"UX bundle file exceeds per-file limit ({max_file_bytes} bytes): "
                f"{path.relative_to(root).as_posix()}"
            )
        total += size
        if total > max_total_bytes:
            raise UXBundleError(f"UX bundle exceeds total size limit ({max_total_bytes} bytes)")
    missing = [
        relative for relative in sorted(_required_manifest_paths(manifest))
        if not (root / relative).is_file()
    ]
    if missing:
        raise UXBundleError("UX bundle references missing file(s): " + ", ".join(missing))
    if manifest.references_root and not (root / manifest.references_root).is_dir():
        raise UXBundleError(
            f"UX bundle references root does not exist: {manifest.references_root}"
        )


def manifest_summary(manifest: UXBundleManifest) -> dict[str, object]:
    return {
        "schema": manifest.schema,
        "product": manifest.product,
        "contract": manifest.contract,
        "prototype": manifest.prototype,
        "journeys": manifest.journeys,
        "principles": manifest.principles,
        "annex_count": len(manifest.annexes),
        "screen_ids": sorted((manifest.screens or {}).keys()),
        "state_ids": sorted((manifest.states or {}).keys()),
        "shared_artifact": manifest.shared_artifact,
        "metadata_keys": sorted((manifest.metadata or {}).keys()),
        "figma_keys": sorted((manifest.figma or {}).keys()),
    }
