from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Callable

from automation.opencode_adapter_contract import (
    AGENT_FILES,
    AUTODEV_ROOT,
    COMMAND_FILES,
    OpenCodeAdapterError,
)


_SCHEDULER_MANIFEST_VERSION = 1
_SCHEDULER_MANIFEST = Path(".git") / "autodev" / "scheduler-runtime-assets" / "opencode.json"
_EXCLUDE = Path(".git") / "info" / "exclude"
_EXCLUDE_BEGIN = "# BEGIN AUTODEV SCHEDULER OPENCODE ASSETS"
_EXCLUDE_END = "# END AUTODEV SCHEDULER OPENCODE ASSETS"


def install_assets(
    target_repo: Path,
    autodev_root: Path = AUTODEV_ROOT,
) -> list[Path]:
    """Install canonical OpenCode commands and agents that invoke `autodev`."""
    target_repo = target_repo.expanduser().resolve()
    autodev_root = autodev_root.expanduser().resolve()
    if not target_repo.is_dir():
        raise OpenCodeAdapterError(f"target repository is not a directory: {target_repo}")

    installed: list[Path] = []
    for relative, source_file in _canonical_assets(autodev_root).items():
        target_file = target_repo / relative
        target_file.parent.mkdir(parents=True, exist_ok=True)
        target_file.write_bytes(source_file.read_bytes())
        installed.append(target_file)
    return installed


def provision_scheduler_worker_assets(
    target_repo: Path,
    autodev_root: Path = AUTODEV_ROOT,
    *,
    runner: Callable[..., object] = subprocess.run,
) -> list[Path]:
    """Provision scheduler-owned OpenCode assets without touching repository-owned files.

    Tracked files are repository-owned and remain authoritative. Untracked assets are
    managed only when absent, already canonical, or recorded in the worker-local
    ownership manifest with an unchanged hash.
    """

    target_repo = target_repo.expanduser().resolve()
    autodev_root = autodev_root.expanduser().resolve()
    if not target_repo.is_dir() or not (target_repo / ".git").exists():
        raise OpenCodeAdapterError(
            f"scheduler OpenCode provisioning requires a Git worker clone: {target_repo}"
        )

    canonical = _canonical_assets(autodev_root)
    previous = _load_scheduler_manifest(target_repo)
    previous_assets = previous.get("assets", {})
    if not isinstance(previous_assets, dict):
        raise OpenCodeAdapterError(
            f"invalid scheduler OpenCode asset manifest: {target_repo / _SCHEDULER_MANIFEST}"
        )

    managed: dict[str, str] = {}
    writes: dict[Path, bytes] = {}
    removals: list[Path] = []

    # Plan the whole operation before mutating the worker. This prevents a later
    # ownership conflict from leaving a half-provisioned visible workspace.
    for relative, source_file in canonical.items():
        relative_text = relative.as_posix()
        target = target_repo / relative
        canonical_bytes = source_file.read_bytes()
        canonical_hash = _sha256(canonical_bytes)
        if _is_tracked(target_repo, relative_text, runner=runner):
            continue
        if target.is_symlink():
            raise OpenCodeAdapterError(
                f"refusing to manage scheduler OpenCode symlink outside repository ownership: {relative_text}"
            )
        if target.exists() and not target.is_file():
            raise OpenCodeAdapterError(
                f"scheduler OpenCode asset path is not a regular file: {relative_text}"
            )

        previous_hash = str(previous_assets.get(relative_text, "") or "")
        if target.is_file():
            current_hash = _sha256(target.read_bytes())
            if previous_hash:
                if current_hash != previous_hash:
                    raise OpenCodeAdapterError(
                        f"scheduler-managed OpenCode asset was modified unexpectedly: {relative_text}; "
                        "AutoDev will not overwrite it"
                    )
            elif current_hash != canonical_hash:
                raise OpenCodeAdapterError(
                    f"untracked OpenCode asset already exists but is not scheduler-owned: {relative_text}; "
                    "move/remove it or track it explicitly before retrying scheduler installation"
                )
            if current_hash != canonical_hash:
                writes[target] = canonical_bytes
        else:
            writes[target] = canonical_bytes
        managed[relative_text] = canonical_hash

    # Remove assets that an older AutoDev version managed but no longer ships,
    # but only while their content still proves scheduler ownership.
    canonical_names = {relative.as_posix() for relative in canonical}
    for relative_text, raw_hash in previous_assets.items():
        relative_text = str(relative_text)
        if relative_text in canonical_names:
            continue
        target = target_repo / relative_text
        if _is_tracked(target_repo, relative_text, runner=runner):
            continue
        if not target.exists():
            continue
        if target.is_symlink() or not target.is_file():
            raise OpenCodeAdapterError(
                f"obsolete scheduler-managed OpenCode asset changed type: {relative_text}; "
                "AutoDev will not remove it"
            )
        if _sha256(target.read_bytes()) != str(raw_hash or ""):
            raise OpenCodeAdapterError(
                f"obsolete scheduler-managed OpenCode asset was modified unexpectedly: {relative_text}; "
                "AutoDev will not remove it"
            )
        removals.append(target)

    _write_exclude_block(target_repo, sorted(managed))
    for target in removals:
        target.unlink()
    for target, data in writes.items():
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + ".autodev-tmp")
        temporary.write_bytes(data)
        temporary.replace(target)

    _save_scheduler_manifest(target_repo, managed)
    return [target_repo / relative for relative in sorted(managed)]


def scheduler_managed_assets(target_repo: Path) -> dict[str, str]:
    value = _load_scheduler_manifest(target_repo.expanduser().resolve())
    assets = value.get("assets", {})
    return {str(key): str(item) for key, item in assets.items()} if isinstance(assets, dict) else {}


def _canonical_assets(autodev_root: Path) -> dict[Path, Path]:
    source_root = autodev_root / "integrations" / "opencode"
    result: dict[Path, Path] = {}
    for directory, names in (("commands", COMMAND_FILES), ("agents", AGENT_FILES)):
        for name in names:
            source_file = source_root / directory / name
            if not source_file.is_file():
                raise OpenCodeAdapterError(f"missing canonical OpenCode asset: {source_file}")
            result[Path(".opencode") / directory / name] = source_file
    return result


def _is_tracked(
    repo: Path,
    relative: str,
    *,
    runner: Callable[..., object],
) -> bool:
    completed = runner(
        ["git", "-C", str(repo), "ls-files", "--error-unmatch", "--", relative],
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    returncode = int(getattr(completed, "returncode", 1))
    if returncode == 0:
        return True
    if returncode == 1:
        return False
    detail = str(getattr(completed, "stderr", "") or "").strip()
    raise OpenCodeAdapterError(
        f"cannot determine repository ownership for scheduler OpenCode asset {relative}: "
        f"git ls-files exited with code {returncode}"
        + (f": {detail}" if detail else "")
    )


def _load_scheduler_manifest(repo: Path) -> dict[str, object]:
    path = repo / _SCHEDULER_MANIFEST
    if not path.is_file():
        return {"schema_version": _SCHEDULER_MANIFEST_VERSION, "assets": {}}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OpenCodeAdapterError(f"invalid scheduler OpenCode asset manifest {path}: {exc}") from exc
    if not isinstance(value, dict) or value.get("schema_version") != _SCHEDULER_MANIFEST_VERSION:
        raise OpenCodeAdapterError(f"unsupported scheduler OpenCode asset manifest: {path}")
    return value


def _save_scheduler_manifest(repo: Path, assets: dict[str, str]) -> None:
    path = repo / _SCHEDULER_MANIFEST
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(
            {
                "schema_version": _SCHEDULER_MANIFEST_VERSION,
                "runtime": "opencode",
                "assets": dict(sorted(assets.items())),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _write_exclude_block(repo: Path, relatives: list[str]) -> None:
    path = repo / _EXCLUDE
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        current = path.read_text(encoding="utf-8") if path.is_file() else ""
    except OSError as exc:
        raise OpenCodeAdapterError(f"cannot read worker Git exclude file {path}: {exc}") from exc

    begin = current.find(_EXCLUDE_BEGIN)
    end = current.find(_EXCLUDE_END)
    if (begin >= 0) != (end >= 0) or (begin >= 0 and end < begin):
        raise OpenCodeAdapterError(
            f"worker Git exclude contains a malformed AutoDev scheduler block: {path}"
        )
    if begin >= 0:
        end += len(_EXCLUDE_END)
        while end < len(current) and current[end] in "\r\n":
            end += 1
        current = current[:begin].rstrip("\r\n") + ("\n" if current[:begin].strip() else "") + current[end:]

    block = ""
    if relatives:
        block = "\n".join([_EXCLUDE_BEGIN, *relatives, _EXCLUDE_END]) + "\n"
    rendered = current
    if rendered and block and not rendered.endswith("\n"):
        rendered += "\n"
    rendered += block
    temporary = path.with_name(path.name + ".autodev-tmp")
    temporary.write_text(rendered, encoding="utf-8")
    temporary.replace(path)


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()
