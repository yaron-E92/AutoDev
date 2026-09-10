from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable

from automation.queue_contract import DONE_LABEL, LABEL_SPECS
from automation.workflow_commands import gh, gh_json


def ensure_done_label(
    repo: Path,
    github_repo: str,
    *,
    runner: Callable[..., object] = subprocess.run,
) -> bool:
    """Ensure the one new label required by already-satisfied completion exists.

    Existing installations may predate ``autodev:done`` even though their
    ordinary queue labels already exist. Keep this upgrade repair local to a
    no-op candidate instead of reconciling or mutating the whole queue.
    """
    if not github_repo.strip():
        return False
    raw = gh_json(
        repo,
        [
            "label",
            "list",
            "--repo",
            github_repo,
            "--limit",
            "1000",
            "--json",
            "name",
        ],
        runner=runner,
    )
    if isinstance(raw, list) and any(
        isinstance(item, dict) and str(item.get("name", "")) == DONE_LABEL
        for item in raw
    ):
        return False

    color, description = LABEL_SPECS[DONE_LABEL]
    gh(
        repo,
        [
            "label",
            "create",
            DONE_LABEL,
            "--repo",
            github_repo,
            "--color",
            color,
            "--description",
            description,
        ],
        runner=runner,
    )
    return True
