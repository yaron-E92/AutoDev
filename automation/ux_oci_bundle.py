from __future__ import annotations

import gzip
import hashlib
import os
import tarfile
from pathlib import Path, PurePosixPath

from automation.ux_contract import (
    DEFAULT_MAX_FILE_BYTES,
    DEFAULT_MAX_FILES,
    DEFAULT_MAX_TOTAL_BYTES,
    UXBundleError,
    load_manifest,
)


ARTIFACT_TYPE = "application/vnd.autodev.ux.bundle.v1"
LAYER_MEDIA_TYPE = "application/vnd.autodev.ux.bundle.v1.tar+gzip"
MAX_ARCHIVE_BYTES = DEFAULT_MAX_TOTAL_BYTES


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _archive_files(root: Path) -> list[Path]:
    root = root.expanduser().resolve()
    load_manifest(root)
    files: list[Path] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        if path.is_symlink():
            raise UXBundleError(
                f"UX bundle publication does not allow symlinks: {path.relative_to(root).as_posix()}"
            )
        if path.is_dir():
            continue
        if not path.is_file():
            raise UXBundleError(
                f"UX bundle publication allows regular files only: {path.relative_to(root).as_posix()}"
            )
        files.append(path)
    if len(files) > DEFAULT_MAX_FILES:
        raise UXBundleError(f"UX bundle exceeds file-count limit ({DEFAULT_MAX_FILES})")
    return files


def write_bundle_archive(root: Path, target: Path) -> None:
    root = root.expanduser().resolve()
    target = target.expanduser().resolve()
    files = _archive_files(root)
    target.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    with target.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as archive:
                for path in files:
                    relative = path.relative_to(root).as_posix()
                    size = path.stat().st_size
                    if size > DEFAULT_MAX_FILE_BYTES:
                        raise UXBundleError(
                            f"UX bundle file exceeds per-file limit ({DEFAULT_MAX_FILE_BYTES} bytes): {relative}"
                        )
                    total += size
                    if total > DEFAULT_MAX_TOTAL_BYTES:
                        raise UXBundleError(
                            f"UX bundle exceeds total size limit ({DEFAULT_MAX_TOTAL_BYTES} bytes)"
                        )
                    info = tarfile.TarInfo(relative)
                    info.size = size
                    info.mode = 0o644
                    info.uid = 0
                    info.gid = 0
                    info.uname = ""
                    info.gname = ""
                    info.mtime = 0
                    with path.open("rb") as source:
                        archive.addfile(info, source)
    if target.stat().st_size > MAX_ARCHIVE_BYTES:
        target.unlink(missing_ok=True)
        raise UXBundleError(
            f"compressed UX bundle exceeds archive size limit ({MAX_ARCHIVE_BYTES} bytes)"
        )


def _safe_member_name(raw: str) -> PurePosixPath:
    value = str(raw or "").replace("\\", "/")
    if not value or "\x00" in value:
        raise UXBundleError("OCI UX archive contains an invalid member path")
    path = PurePosixPath(value)
    drive_like = bool(path.parts and ":" in path.parts[0])
    if path.is_absolute() or drive_like or ".." in path.parts or "." in path.parts:
        raise UXBundleError(f"OCI UX archive contains unsafe member path: {value!r}")
    if any(part in {"", "~"} for part in path.parts):
        raise UXBundleError(f"OCI UX archive contains unsafe member path: {value!r}")
    return path


def safe_extract_bundle_archive(archive_path: Path, destination: Path) -> None:
    archive_path = archive_path.expanduser().resolve()
    destination = destination.expanduser().resolve()
    if archive_path.stat().st_size > MAX_ARCHIVE_BYTES:
        raise UXBundleError(
            f"OCI UX archive exceeds compressed size limit ({MAX_ARCHIVE_BYTES} bytes)"
        )
    destination.mkdir(parents=True, exist_ok=True)
    count = 0
    total = 0
    seen: set[str] = set()
    with tarfile.open(archive_path, mode="r:gz") as archive:
        for member in archive:
            path = _safe_member_name(member.name)
            relative = path.as_posix()
            if relative in seen:
                raise UXBundleError(f"OCI UX archive contains duplicate member: {relative}")
            seen.add(relative)
            if member.isdir():
                target = destination.joinpath(*path.parts)
                target.mkdir(parents=True, exist_ok=True)
                continue
            if not member.isfile():
                raise UXBundleError(
                    f"OCI UX archive contains unsupported non-file member: {relative}"
                )
            count += 1
            if count > DEFAULT_MAX_FILES:
                raise UXBundleError(
                    f"OCI UX archive exceeds file-count limit ({DEFAULT_MAX_FILES})"
                )
            if member.size < 0 or member.size > DEFAULT_MAX_FILE_BYTES:
                raise UXBundleError(
                    f"OCI UX archive member exceeds per-file limit ({DEFAULT_MAX_FILE_BYTES} bytes): {relative}"
                )
            total += member.size
            if total > DEFAULT_MAX_TOTAL_BYTES:
                raise UXBundleError(
                    f"OCI UX archive exceeds total size limit ({DEFAULT_MAX_TOTAL_BYTES} bytes)"
                )
            target = destination.joinpath(*path.parts)
            resolved_parent = target.parent.resolve()
            try:
                resolved_parent.relative_to(destination)
            except ValueError as exc:
                raise UXBundleError(
                    f"OCI UX archive member escapes destination: {relative}"
                ) from exc
            target.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(member)
            if source is None:
                raise UXBundleError(f"cannot read OCI UX archive member: {relative}")
            remaining = member.size
            with target.open("xb") as output:
                while remaining:
                    chunk = source.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise UXBundleError(
                            f"OCI UX archive member ended early: {relative}"
                        )
                    output.write(chunk)
                    remaining -= len(chunk)
                if source.read(1):
                    raise UXBundleError(
                        f"OCI UX archive member exceeds declared size: {relative}"
                    )
            try:
                target.chmod(0o644)
            except OSError:
                pass
    load_manifest(destination)
