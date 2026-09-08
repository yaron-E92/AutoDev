from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable


CAPTURE_SCHEMA = "autodev.ux.capture/v1"
CAPTURE_CONFIG = Path(".autodev") / "ux-capture.json"
DEFAULT_TIMEOUT_SECONDS = 120
MAX_TIMEOUT_SECONDS = 600
MAX_IMAGE_BYTES = 20 * 1024 * 1024
_SUPPORTED_KINDS = {"screen", "state", "journey"}
_SAFE_ID = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")


class UXCaptureError(RuntimeError):
    pass


@dataclass(frozen=True)
class CaptureTarget:
    source_kind: str
    source_id: str
    output_name: str
    viewport: str = ""
    platform: str = ""

    @property
    def target_id(self) -> str:
        return f"{self.source_kind}:{self.source_id}"


@dataclass(frozen=True)
class CaptureConfig:
    command: tuple[str, ...]
    targets: dict[str, CaptureTarget]
    timeout_seconds: int
    sha256: str


@dataclass(frozen=True)
class CapturedImage:
    target: CaptureTarget
    path: Path
    sha256: str
    mime: str
    size_bytes: int


def load_config(repo: Path) -> CaptureConfig | None:
    repo = repo.expanduser().resolve()
    path = repo / CAPTURE_CONFIG
    if not path.is_file():
        return None
    try:
        raw_bytes = path.read_bytes()
        value = json.loads(raw_bytes.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UXCaptureError(f"UX capture configuration is unreadable or malformed: {path}") from exc
    if not isinstance(value, dict):
        raise UXCaptureError("UX capture configuration must be a JSON object")
    if str(value.get("schema", "") or "") != CAPTURE_SCHEMA:
        raise UXCaptureError(
            f"unsupported UX capture schema {value.get('schema')!r}; expected {CAPTURE_SCHEMA!r}"
        )
    provider = str(value.get("provider", "command") or "command").strip().casefold()
    if provider != "command":
        raise UXCaptureError(f"unsupported UX capture provider: {provider!r}")
    command_value = value.get("command")
    if not isinstance(command_value, list) or not command_value:
        raise UXCaptureError("UX capture command must be a non-empty JSON string array")
    if len(command_value) > 32 or not all(isinstance(item, str) and item.strip() for item in command_value):
        raise UXCaptureError("UX capture command contains invalid arguments")
    command = tuple(item.strip() for item in command_value)
    timeout = int(value.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS) or DEFAULT_TIMEOUT_SECONDS)
    if not 1 <= timeout <= MAX_TIMEOUT_SECONDS:
        raise UXCaptureError(
            f"UX capture timeout_seconds must be between 1 and {MAX_TIMEOUT_SECONDS}"
        )
    raw_targets = value.get("targets", {})
    if not isinstance(raw_targets, dict) or not raw_targets:
        raise UXCaptureError("UX capture configuration must define at least one target")
    if len(raw_targets) > 128:
        raise UXCaptureError("UX capture configuration defines too many targets")
    targets: dict[str, CaptureTarget] = {}
    for raw_key, raw_target in raw_targets.items():
        key = str(raw_key or "").strip()
        if not isinstance(raw_target, dict):
            raise UXCaptureError(f"UX capture target {key!r} must be a JSON object")
        kind = str(raw_target.get("source_kind", "") or "").strip().casefold()
        source_id = str(raw_target.get("source_id", "") or "").strip()
        output_name = str(raw_target.get("output", "") or "").strip().replace("\\", "/")
        viewport = str(raw_target.get("viewport", "") or "").strip()
        platform = str(raw_target.get("platform", "") or "").strip()
        target = _validate_target(kind, source_id, output_name, viewport, platform)
        if key not in {target.target_id, target.source_id}:
            raise UXCaptureError(
                f"UX capture target key {key!r} must equal {target.target_id!r} or {target.source_id!r}"
            )
        if target.target_id in targets:
            raise UXCaptureError(f"duplicate UX capture target: {target.target_id}")
        targets[target.target_id] = target
    return CaptureConfig(
        command=command,
        targets=targets,
        timeout_seconds=timeout,
        sha256=hashlib.sha256(raw_bytes).hexdigest(),
    )


def _validate_target(
    source_kind: str,
    source_id: str,
    output_name: str,
    viewport: str,
    platform: str,
) -> CaptureTarget:
    if source_kind not in _SUPPORTED_KINDS:
        raise UXCaptureError(
            f"UX capture source_kind must be one of {', '.join(sorted(_SUPPORTED_KINDS))}"
        )
    if not _SAFE_ID.fullmatch(source_id):
        raise UXCaptureError(f"unsafe UX capture source_id: {source_id!r}")
    path = PurePosixPath(output_name)
    if (
        not output_name
        or path.is_absolute()
        or len(path.parts) != 1
        or path.name in {".", ".."}
        or path.suffix.casefold() not in {".png", ".jpg", ".jpeg", ".gif", ".webp"}
    ):
        raise UXCaptureError(
            "UX capture output must be a single safe PNG/JPEG/GIF/WebP filename"
        )
    if len(viewport) > 64 or len(platform) > 64:
        raise UXCaptureError("UX capture viewport/platform metadata is too long")
    return CaptureTarget(
        source_kind=source_kind,
        source_id=source_id,
        output_name=path.name,
        viewport=viewport,
        platform=platform,
    )


def capture_target(
    repo: Path,
    current: Path,
    config: CaptureConfig,
    target_id: str,
    *,
    runner: Callable[..., object] = subprocess.run,
) -> CapturedImage:
    repo = repo.expanduser().resolve()
    current = current.expanduser().resolve()
    target = config.targets.get(target_id)
    if target is None:
        raise UXCaptureError(
            f"UX capture configuration has no deterministic target for {target_id}"
        )
    output_dir = current / "ux-captures"
    output_dir.mkdir(parents=True, exist_ok=True)
    output = (output_dir / target.output_name).resolve()
    try:
        output.relative_to(output_dir.resolve())
    except ValueError as exc:
        raise UXCaptureError("UX capture output escapes the run capture directory") from exc
    output.unlink(missing_ok=True)

    environment = dict(os.environ)
    environment.update(
        {
            "AUTODEV_UX_CAPTURE_TARGET": target.target_id,
            "AUTODEV_UX_CAPTURE_SOURCE_KIND": target.source_kind,
            "AUTODEV_UX_CAPTURE_SOURCE_ID": target.source_id,
            "AUTODEV_UX_CAPTURE_OUTPUT": str(output),
            "AUTODEV_UX_CAPTURE_VIEWPORT": target.viewport,
            "AUTODEV_UX_CAPTURE_PLATFORM": target.platform,
        }
    )
    try:
        completed = runner(
            list(config.command),
            cwd=repo,
            env=environment,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            timeout=config.timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise UXCaptureError(
            f"UX capture timed out for {target.target_id} after {config.timeout_seconds} seconds"
        ) from exc
    except OSError as exc:
        raise UXCaptureError(
            f"UX capture command could not be launched for {target.target_id}: {exc}"
        ) from exc
    returncode = int(getattr(completed, "returncode", 1))
    if returncode != 0:
        detail = _bounded(str(getattr(completed, "stderr", "") or ""))
        raise UXCaptureError(
            f"UX capture command failed for {target.target_id} with exit code {returncode}"
            + (f": {detail}" if detail else "")
        )
    if not output.is_file():
        raise UXCaptureError(
            f"UX capture command did not produce the declared image for {target.target_id}: {output}"
        )
    data = output.read_bytes()
    if not data:
        raise UXCaptureError(f"UX capture image is empty for {target.target_id}")
    if len(data) > MAX_IMAGE_BYTES:
        raise UXCaptureError(
            f"UX capture image exceeds {MAX_IMAGE_BYTES} bytes for {target.target_id}"
        )
    mime = image_mime(data)
    if not mime:
        raise UXCaptureError(
            f"UX capture output is not a supported PNG/JPEG/GIF/WebP image for {target.target_id}"
        )
    return CapturedImage(
        target=target,
        path=output,
        sha256=hashlib.sha256(data).hexdigest(),
        mime=mime,
        size_bytes=len(data),
    )


def image_mime(data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return ""


def _bounded(value: str, limit: int = 1000) -> str:
    return " ".join(str(value or "").split())[:limit]
