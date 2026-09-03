from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator

from automation.ux_contract import UXBundleError, load_manifest


CACHE_META = ".autodev-ux-cache.json"
CACHE_VERSION = 1


class UXCacheError(RuntimeError):
    pass


def cache_root() -> Path:
    explicit = os.environ.get("AUTODEV_CACHE_HOME", "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve() / "ux"
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA", "").strip()
        base = Path(local).expanduser() if local else Path.home() / "AppData" / "Local"
        return (base / "AutoDev" / "Cache" / "ux").resolve()
    xdg = os.environ.get("XDG_CACHE_HOME", "").strip()
    base = Path(xdg).expanduser() if xdg else Path.home() / ".cache"
    return (base / "autodev" / "ux").resolve()


def identity_key(identity: str) -> str:
    value = identity.strip()
    if not value:
        raise UXCacheError("UX cache identity must be non-empty")
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def entry_path(identity: str, *, root: Path | None = None) -> Path:
    base = (root or cache_root()).expanduser().resolve()
    return base / identity_key(identity)


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(
        (item for item in root.rglob("*") if item.is_file() and item.name != CACHE_META),
        key=lambda item: item.relative_to(root).as_posix(),
    ):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(path.stat().st_size.to_bytes(8, "big"))
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def validate_entry(path: Path, identity: str) -> bool:
    path = path.expanduser().resolve()
    meta_path = path / CACHE_META
    if not meta_path.is_file():
        return False
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(meta, dict):
        return False
    if meta.get("version") != CACHE_VERSION or meta.get("identity") != identity:
        return False
    expected = str(meta.get("tree_sha256", "") or "")
    if not expected:
        return False
    try:
        load_manifest(path)
        return _tree_digest(path) == expected
    except (OSError, UXBundleError):
        return False


@contextmanager
def _entry_lock(
    identity: str,
    *,
    root: Path,
    timeout_seconds: float = 30.0,
) -> Iterator[None]:
    root.mkdir(parents=True, exist_ok=True)
    lock = root / (identity_key(identity) + ".lock")
    deadline = time.monotonic() + max(timeout_seconds, 0.1)
    while True:
        try:
            lock.mkdir()
            break
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise UXCacheError(f"timed out waiting for UX cache lock for {identity}")
            time.sleep(0.05)
    try:
        yield
    finally:
        try:
            lock.rmdir()
        except OSError:
            pass


def populate(
    identity: str,
    producer: Callable[[Path], None],
    *,
    root: Path | None = None,
) -> tuple[Path, bool]:
    base = (root or cache_root()).expanduser().resolve()
    target = entry_path(identity, root=base)
    with _entry_lock(identity, root=base):
        if validate_entry(target, identity):
            return target, True
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        staging = Path(
            tempfile.mkdtemp(prefix=identity_key(identity) + ".", dir=str(base))
        )
        try:
            producer(staging)
            load_manifest(staging)
            tree_digest = _tree_digest(staging)
            (staging / CACHE_META).write_text(
                json.dumps(
                    {
                        "version": CACHE_VERSION,
                        "identity": identity,
                        "tree_sha256": tree_digest,
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)
            os.replace(staging, target)
            return target, False
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise


def remove_corrupt_entries(*, root: Path | None = None) -> int:
    base = (root or cache_root()).expanduser().resolve()
    if not base.is_dir():
        return 0
    removed = 0
    for child in base.iterdir():
        if not child.is_dir() or child.name.endswith(".lock"):
            continue
        meta_path = child / CACHE_META
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            meta = {}
        identity = str(meta.get("identity", "") or "") if isinstance(meta, dict) else ""
        if not identity or not validate_entry(child, identity):
            shutil.rmtree(child, ignore_errors=True)
            removed += 1
    return removed


def prune(
    *,
    max_entries: int,
    root: Path | None = None,
) -> tuple[str, ...]:
    if max_entries < 0:
        raise UXCacheError("max_entries must be zero or greater")
    base = (root or cache_root()).expanduser().resolve()
    if not base.is_dir():
        return ()
    entries = [
        path
        for path in base.iterdir()
        if path.is_dir() and not path.name.endswith(".lock") and (path / CACHE_META).is_file()
    ]
    entries.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    removed: list[str] = []
    for path in entries[max_entries:]:
        removed.append(path.name)
        shutil.rmtree(path, ignore_errors=True)
    return tuple(removed)
