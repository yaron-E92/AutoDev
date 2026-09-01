from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


SCHEDULER_SCHEMA = 1
DEFAULT_CADENCE_MINUTES = 15
MIN_CADENCE_MINUTES = 1
MAX_CADENCE_MINUTES = 59
STATE_ROOT = Path(".autodev") / "schedulers"
WORKER_ROOT = Path(".autodev") / "workers"
REGISTRATION_FILE = "registration.json"
LOCK_FILE = "run.lock"
LOG_FILE = "scheduler.log"
BACKEND_AUTO = "auto"
BACKEND_SYSTEMD = "systemd-user"
BACKEND_CRON = "cron"
BACKEND_WINDOWS = "windows-task"
SUPPORTED_BACKENDS = {BACKEND_SYSTEMD, BACKEND_CRON, BACKEND_WINDOWS}
_REPO_ID = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class SchedulerError(RuntimeError):
    pass


@dataclass(frozen=True)
class SchedulerRegistration:
    github_repository: str
    source_repository: str
    worker_repository: str
    default_branch: str
    backend: str
    cadence_minutes: int
    launcher: str
    task_id: str
    installed_at: str
    last_run: dict[str, object] | None = None

    def to_json(self) -> dict[str, object]:
        return {
            "schema_version": SCHEDULER_SCHEMA,
            **asdict(self),
        }


@dataclass(frozen=True)
class SchedulerStatus:
    state: str
    github_repository: str = ""
    backend: str = ""
    backend_state: str = ""
    source_repository: str = ""
    worker_repository: str = ""
    worker_exists: bool = False
    cadence_minutes: int = 0
    last_run: dict[str, object] | None = None

    def to_json(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class DispatchResult:
    state: str
    github_repository: str
    selection_state: str = ""
    issue_number: int = 0
    coordinator_exit_code: int | None = None
    coordinator_state: str = ""
    claim_state: str = ""
    claim_worker_id: str = ""
    claim_run_id: str = ""
    detail: str = ""

    def to_json(self) -> dict[str, object]:
        return asdict(self)


def _dispatch_state(
    coordinator_state: str,
    *,
    coordinator_exit_code: int = 0,
) -> str:
    normalized = coordinator_state.casefold().replace("_", "").replace("-", "")
    if normalized in {"readyforreview", "prready"}:
        return "PR_READY"
    if normalized in {"attentionrequired", "attention"}:
        return "ATTENTION_REQUIRED"
    if normalized in {"blocked", "failed", "terminalfailed"}:
        return "RUN_HEALTH_BLOCKED"
    if coordinator_exit_code != 0:
        return "RUN_HEALTH_BLOCKED"
    return "DISPATCHED"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _platform_name(value: str | None = None) -> str:
    raw = (value or ("windows" if os.name == "nt" else "posix")).casefold()
    if raw not in {"windows", "posix"}:
        raise SchedulerError(f"unsupported scheduler platform: {raw}")
    return raw


def _repo_parts(github_repo: str) -> tuple[str, str]:
    value = github_repo.strip()
    if not _REPO_ID.fullmatch(value):
        raise SchedulerError("GitHub repository identity must use owner/name format")
    owner, name = value.split("/", 1)
    if owner in {".", ".."} or name in {".", ".."}:
        raise SchedulerError("unsafe GitHub repository identity")
    return owner, name


def registration_path(github_repo: str, *, home: Path | None = None) -> Path:
    owner, name = _repo_parts(github_repo)
    root = (home or Path.home()).expanduser().resolve()
    return root / STATE_ROOT / owner / name / REGISTRATION_FILE


def worker_path(github_repo: str, *, home: Path | None = None) -> Path:
    owner, name = _repo_parts(github_repo)
    root = (home or Path.home()).expanduser().resolve()
    return root / WORKER_ROOT / owner / name


def _task_id(github_repo: str) -> str:
    owner, name = _repo_parts(github_repo)
    raw = f"{owner}-{name}".casefold()
    slug = re.sub(r"[^a-z0-9_.-]+", "-", raw).strip("-.") or "repo"
    return "autodev-" + slug[:80]


def _repo_root(path: Path) -> Path:
    repo = path.expanduser().resolve()
    if not repo.is_dir() or not (repo / ".git").exists():
        raise SchedulerError(f"not a Git repository root: {repo}")
    return repo
