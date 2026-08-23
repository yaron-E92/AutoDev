from __future__ import annotations

import json
import shutil
from pathlib import Path

from automation.opencode_adapter_contract import (
    AGENT_FILES,
    AUTODEV_ROOT,
    COMMAND_FILES,
    OpenCodeAdapterError,
)

def install_assets(
    target_repo: Path,
    autodev_root: Path = AUTODEV_ROOT,
    *,
    python_command: str = "python",
) -> list[Path]:
    """Install the low-level OpenCode bridge assets.

    This remains an internal primitive for automation.opencode_install. User-facing
    installation is handled by automation.opencode_install so every required
    integration asset is installed together.
    """
    target_repo = target_repo.expanduser().resolve()
    autodev_root = autodev_root.expanduser().resolve()
    if not target_repo.is_dir():
        raise OpenCodeAdapterError(f"target repository is not a directory: {target_repo}")

    source = autodev_root / "integrations" / "opencode"
    target = target_repo / ".opencode"
    installed: list[Path] = []
    for directory, names in (("commands", COMMAND_FILES), ("agents", AGENT_FILES)):
        destination = target / directory
        destination.mkdir(parents=True, exist_ok=True)
        for name in names:
            source_file = source / directory / name
            if not source_file.is_file():
                raise OpenCodeAdapterError(f"missing canonical OpenCode asset: {source_file}")
            target_file = destination / name
            shutil.copyfile(source_file, target_file)
            installed.append(target_file)

    target.mkdir(parents=True, exist_ok=True)
    for wrapper_name in ("autodev.py", "autodev.ps1"):
        wrapper_source = source / wrapper_name
        if not wrapper_source.is_file():
            raise OpenCodeAdapterError(f"missing canonical OpenCode bridge wrapper: {wrapper_source}")
        wrapper_target = target / wrapper_name
        shutil.copyfile(wrapper_source, wrapper_target)
        installed.append(wrapper_target)

    config_path = target / "autodev.json"
    config_path.write_text(
        json.dumps(
            {
                "version": 1,
                "autodev_root": str(autodev_root),
                "python": python_command,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    installed.append(config_path)
    return installed
