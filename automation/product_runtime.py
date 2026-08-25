from __future__ import annotations

import json
import os
import sys
from pathlib import Path


BUILD_INFO_FILE = "autodev-build.json"
DEVELOPMENT_VERSION = "development"


def product_root() -> Path:
    """Return the root that owns bundled AutoDev data files."""
    return Path(__file__).resolve().parents[1]


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def build_info(root: Path | None = None) -> dict[str, str]:
    candidates = [
        (root or product_root()) / BUILD_INFO_FILE,
    ]
    if is_frozen():
        candidates.append(Path(sys.executable).resolve().parent / BUILD_INFO_FILE)

    for path in candidates:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(raw, dict):
            continue
        version = str(raw.get("version", "")).strip()
        commit = str(raw.get("commit_sha", "")).strip()
        if version:
            return {"version": version, "commit_sha": commit}
    return {}


def version(root: Path | None = None) -> str:
    override = os.environ.get("AUTODEV_VERSION", "").strip()
    if override:
        return override
    return build_info(root).get("version", DEVELOPMENT_VERSION)


def commit_sha(root: Path | None = None) -> str:
    return build_info(root).get("commit_sha", "")


def version_text(root: Path | None = None) -> str:
    value = version(root)
    commit = commit_sha(root)
    return f"autodev {value}" + (f" ({commit[:12]})" if commit else "")
