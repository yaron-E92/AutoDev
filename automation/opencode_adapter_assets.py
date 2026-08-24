from __future__ import annotations

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
) -> list[Path]:
    """Install canonical OpenCode commands and agents that invoke `autodev`."""
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
    return installed
