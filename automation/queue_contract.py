from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path


MANAGED_LABEL = "autodev:managed"

READY_LABEL = "autodev:ready"

BLOCKED_LABEL = "autodev:blocked"

ATTENTION_LABEL = "autodev:attention"

RUNNING_LABEL = "autodev:running"

QUEUE_CONFIG = Path(".autodev") / "queue.json"

API_VERSION = "2026-03-10"

DEFAULT_LIMIT = 1000

LABEL_SPECS = {
    MANAGED_LABEL: ("1d76db", "Human authorization for autonomous AutoDev work"),
    READY_LABEL: ("0e8a16", "Derived: managed and currently runnable by AutoDev"),
    BLOCKED_LABEL: ("d93f0b", "Derived: managed but blocked by open issue dependencies"),
    ATTENTION_LABEL: ("fbca04", "Human attention is required before autonomous AutoDev work"),
    RUNNING_LABEL: ("5319e7", "Active AutoDev claim/run for this issue"),
}

class QueueError(RuntimeError):
    pass

@dataclass(frozen=True)
class QueuePolicy:
    autonomous_execution: bool = True

@dataclass(frozen=True)
class QueueIssue:
    number: int
    title: str
    url: str
    state: str
    labels: tuple[str, ...]
    created_at: str = ""
    milestone: str = ""

@dataclass(frozen=True)
class Blocker:
    id: int
    number: int
    title: str
    url: str
    state: str

@dataclass(frozen=True)
class QueueState:
    issue: QueueIssue
    reason: str
    open_blockers: tuple[Blocker, ...] = ()
    closed_blockers: tuple[Blocker, ...] = ()
    changed: bool = False
    removed_closed_dependencies: tuple[int, ...] = ()

@dataclass(frozen=True)
class CommandResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str

def _label_names(raw: object) -> tuple[str, ...]:
    if not isinstance(raw, list):
        return ()
    names: list[str] = []
    for item in raw:
        if isinstance(item, dict) and item.get("name"):
            names.append(str(item["name"]))
        elif isinstance(item, str) and item:
            names.append(item)
    return tuple(sorted(set(names)))

def _milestone_title(raw: object) -> str:
    if isinstance(raw, dict):
        return str(raw.get("title", ""))
    if isinstance(raw, str):
        return raw
    return ""
