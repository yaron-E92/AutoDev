from __future__ import annotations

import math
import subprocess
from pathlib import Path
from typing import Callable

from automation.repair_budget_contract import (
    _BINARY_SUFFIXES,
    _GENERATED_PREFIXES,
)

def change_metrics(
    repo: Path,
    state: dict[str, object],
    *,
    runner: Callable[..., object] = subprocess.run,
) -> dict[str, object]:
    repo = repo.expanduser().resolve()
    changes = state.get("VerifiedChanges", [])
    changes = changes if isinstance(changes, list) else []
    base_sha = str(state.get("VerifiedParentSha", "") or state.get("BaseSha", "")).strip()

    additions = 0
    deletions = 0
    weighted_total = 0.0
    eligible_paths: list[str] = []
    skipped_generated: list[str] = []
    skipped_binary: list[str] = []

    for item in changes:
        if not isinstance(item, dict):
            continue
        relative = str(item.get("path", item.get("Path", ""))).replace("\\", "/").removeprefix("./")
        status = str(item.get("status", item.get("Status", ""))).casefold()
        if not relative:
            continue
        if _generated(relative):
            skipped_generated.append(relative)
            continue
        if Path(relative).suffix.casefold() in _BINARY_SUFFIXES:
            skipped_binary.append(relative)
            continue

        added, deleted, binary = _changed_lines(repo, base_sha, relative, status, runner=runner)
        if binary:
            skipped_binary.append(relative)
            continue
        eligible_paths.append(relative)
        additions += added
        deletions += deleted
        weighted_total += (added + deleted) * _path_weight(relative)

    return {
        "changed_file_count": len(eligible_paths),
        "eligible_paths": sorted(eligible_paths),
        "skipped_generated_paths": sorted(skipped_generated),
        "skipped_binary_paths": sorted(skipped_binary),
        "added_lines": additions,
        "deleted_lines": deletions,
        "weighted_changed_lines": math.ceil(weighted_total),
    }

def _changed_lines(
    repo: Path,
    base_sha: str,
    relative: str,
    status: str,
    *,
    runner: Callable[..., object],
) -> tuple[int, int, bool]:
    if base_sha:
        try:
            completed = runner(
                ["git", "diff", "--numstat", "--no-renames", "-z", base_sha, "--", relative],
                cwd=repo,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        except OSError:
            completed = None
        if completed is not None and int(getattr(completed, "returncode", 1)) == 0:
            raw = getattr(completed, "stdout", b"") or b""
            if isinstance(raw, str):
                raw = raw.encode("utf-8", errors="surrogateescape")
            record = raw.split(b"\0", 1)[0]
            fields = record.split(b"\t", 2)
            if len(fields) >= 2:
                if fields[0] == b"-" or fields[1] == b"-":
                    return 0, 0, True
                try:
                    return int(fields[0]), int(fields[1]), False
                except ValueError:
                    pass

    path = repo / relative
    if status == "deleted" or not path.is_file():
        return 0, 0, False
    try:
        data = path.read_bytes()
    except OSError:
        return 0, 0, False
    if b"\0" in data[:8192]:
        return 0, 0, True
    return _line_count(data), 0, False

def _line_count(data: bytes) -> int:
    if not data:
        return 0
    return data.count(b"\n") + (0 if data.endswith(b"\n") else 1)

def _generated(relative: str) -> bool:
    normalized = relative.casefold().replace("\\", "/").removeprefix("./")
    return any(
        normalized.startswith(prefix) or f"/{prefix}" in f"/{normalized}"
        for prefix in _GENERATED_PREFIXES
    )

def _path_weight(relative: str) -> float:
    normalized = relative.casefold().replace("\\", "/")
    name = Path(normalized).name
    if (
        normalized.startswith("tests/")
        or "/tests/" in f"/{normalized}"
        or name.startswith("test_")
        or name.endswith("tests.cs")
        or name.endswith("test.cs")
        or name.endswith(".spec.ts")
        or name.endswith(".test.ts")
        or name.endswith(".spec.js")
        or name.endswith(".test.js")
    ):
        return 0.5
    if Path(normalized).suffix in {".md", ".rst", ".txt"}:
        return 0.25
    return 1.0
