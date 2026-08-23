from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, TextIO

from automation import issue_queue


CLAIM_SCHEMA = 1
WORKER_SCHEMA = 1
CLAIM_MESSAGE = "AUTODEV_DISTRIBUTED_CLAIM_V1"
CLAIM_REF_PREFIX = "refs/heads/autodev/claims/issue-"
WORKER_STATE = Path(".autodev") / "worker.json"
DEFAULT_MAX_CONCURRENT_ISSUES = 1
DEFAULT_LEASE_MINUTES = 120
MIN_LEASE_MINUTES = 15
MAX_LEASE_MINUTES = 24 * 60
MAX_CONCURRENT_ISSUES = 16
WORKER_ID_ENV = "AUTODEV_WORKER_ID"
_WORKER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_ZERO_SHA = "0" * 40


class ClaimError(RuntimeError):
    pass


@dataclass(frozen=True)
class ClaimPolicy:
    max_concurrent_issues: int = DEFAULT_MAX_CONCURRENT_ISSUES
    lease_minutes: int = DEFAULT_LEASE_MINUTES


@dataclass(frozen=True)
class Claim:
    repository: str
    issue_number: int
    worker_id: str
    run_id: str
    claim_id: str
    acquired_at: str
    heartbeat_at: str
    lease_seconds: int
    ref: str
    sha: str

    def to_json(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class ClaimAttempt:
    state: str
    claim: Claim | None = None
    owner: Claim | None = None
    detail: str = ""


@dataclass(frozen=True)
class RecoveryResult:
    recovered: tuple[int, ...] = ()
    protected: tuple[int, ...] = ()
    raced: tuple[int, ...] = ()


@dataclass(frozen=True)
class WorkerIdentity:
    worker_id: str

    def to_json(self) -> dict[str, object]:
        return {"schema_version": WORKER_SCHEMA, "worker_id": self.worker_id}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_time(value: str) -> datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ClaimError(f"invalid claim timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def claim_ref(issue_number: int) -> str:
    if issue_number <= 0:
        raise ClaimError("claim issue number must be positive")
    return f"{CLAIM_REF_PREFIX}{issue_number}"


def _validate_worker_id(value: str) -> str:
    worker_id = value.strip()
    if not _WORKER_ID.fullmatch(worker_id):
        raise ClaimError(
            "worker identity must be 1-64 characters using letters, digits, '.', '_' or '-', and start with a letter or digit"
        )
    return worker_id


def worker_state_path(*, home: Path | None = None) -> Path:
    return (home or Path.home()).expanduser().resolve() / WORKER_STATE


def set_worker_identity(worker_id: str, *, home: Path | None = None) -> WorkerIdentity:
    identity = WorkerIdentity(_validate_worker_id(worker_id))
    path = worker_state_path(home=home)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(identity.to_json(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
    return identity


def worker_identity(*, home: Path | None = None, create: bool = True) -> WorkerIdentity:
    override = os.environ.get(WORKER_ID_ENV, "").strip()
    if override:
        return WorkerIdentity(_validate_worker_id(override))
    path = worker_state_path(home=home)
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ClaimError(f"invalid AutoDev worker identity file: {path}") from exc
        if not isinstance(raw, dict) or raw.get("schema_version") != WORKER_SCHEMA:
            raise ClaimError(f"unsupported AutoDev worker identity schema: {path}")
        return WorkerIdentity(_validate_worker_id(str(raw.get("worker_id", ""))))
    if not create:
        raise ClaimError("AutoDev worker identity is not configured")
    generated = f"worker-{uuid.uuid4().hex[:12]}"
    return set_worker_identity(generated, home=home)


def load_claim_policy(repo: Path) -> ClaimPolicy:
    repo = repo.expanduser().resolve()
    # Keep the queue parser authoritative for core policy validity while allowing
    # the distributed-claim extension to remain backwards-compatible with v1 files.
    issue_queue.load_policy(repo)
    path = repo / issue_queue.QUEUE_CONFIG
    if not path.is_file():
        return ClaimPolicy()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ClaimError(f"invalid queue policy JSON: {path}") from exc
    if not isinstance(raw, dict):
        raise ClaimError(f"queue policy must be a JSON object: {path}")
    concurrency = raw.get("max_concurrent_issues", DEFAULT_MAX_CONCURRENT_ISSUES)
    lease = raw.get("claim_lease_minutes", DEFAULT_LEASE_MINUTES)
    if not isinstance(concurrency, int) or isinstance(concurrency, bool):
        raise ClaimError("queue policy max_concurrent_issues must be an integer")
    if not 1 <= concurrency <= MAX_CONCURRENT_ISSUES:
        raise ClaimError(
            f"queue policy max_concurrent_issues must be between 1 and {MAX_CONCURRENT_ISSUES}"
        )
    if not isinstance(lease, int) or isinstance(lease, bool):
        raise ClaimError("queue policy claim_lease_minutes must be an integer")
    if not MIN_LEASE_MINUTES <= lease <= MAX_LEASE_MINUTES:
        raise ClaimError(
            f"queue policy claim_lease_minutes must be between {MIN_LEASE_MINUTES} and {MAX_LEASE_MINUTES}"
        )
    return ClaimPolicy(max_concurrent_issues=concurrency, lease_minutes=lease)


def _run(
    repo: Path,
    argv: list[str],
    *,
    runner: Callable[..., object] = subprocess.run,
    input_text: str | None = None,
) -> object:
    kwargs: dict[str, object] = {
        "cwd": repo,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "capture_output": True,
        "check": False,
    }
    if input_text is not None:
        kwargs["input"] = input_text
    try:
        return runner(argv, **kwargs)
    except OSError as exc:
        raise ClaimError(f"cannot execute {argv[0]}: {exc}") from exc


def _returncode(completed: object) -> int:
    return int(getattr(completed, "returncode", 1))


def _stdout(completed: object) -> str:
    return str(getattr(completed, "stdout", "") or "")


def _stderr(completed: object) -> str:
    return str(getattr(completed, "stderr", "") or "")


def _require_ok(completed: object, argv: list[str]) -> object:
    if _returncode(completed) != 0:
        detail = _stderr(completed).strip() or _stdout(completed).strip() or "no command output"
        raise ClaimError(f"command failed ({_returncode(completed)}): {' '.join(argv)}: {detail}")
    return completed


def _git(
    repo: Path,
    args: list[str],
    *,
    runner: Callable[..., object] = subprocess.run,
    input_text: str | None = None,
    check: bool = True,
) -> object:
    argv = ["git", "-C", str(repo), *args]
    result = _run(repo, argv, runner=runner, input_text=input_text)
    return _require_ok(result, argv) if check else result


def _is_push_race(result: object) -> bool:
    if _returncode(result) == 0:
        return False
    text = (_stdout(result) + "\n" + _stderr(result)).casefold()
    markers = (
        "stale info",
        "non-fast-forward",
        "fetch first",
        "[rejected]",
        "cannot lock ref",
        "failed to push some refs",
        "remote ref does not exist",
    )
    return any(marker in text for marker in markers)


def _remote_ref_sha(
    repo: Path,
    ref: str,
    *,
    runner: Callable[..., object] = subprocess.run,
) -> str:
    result = _git(repo, ["ls-remote", "--heads", "origin", ref], runner=runner)
    lines = [line.strip() for line in _stdout(result).splitlines() if line.strip()]
    for line in lines:
        fields = line.split()
        if len(fields) >= 2 and fields[1] == ref:
            return fields[0]
    return ""


def _claim_message(metadata: dict[str, object]) -> str:
    return CLAIM_MESSAGE + "\n" + json.dumps(metadata, sort_keys=True, separators=(",", ":")) + "\n"


def _parse_claim_message(message: str, *, ref: str, sha: str) -> Claim:
    lines = message.splitlines()
    if not lines or lines[0].strip() != CLAIM_MESSAGE:
        raise ClaimError(f"remote AutoDev claim ref contains unrecognized metadata: {ref}")
    payload_text = "\n".join(lines[1:]).strip()
    try:
        raw = json.loads(payload_text)
    except json.JSONDecodeError as exc:
        raise ClaimError(f"remote AutoDev claim metadata is invalid JSON: {ref}") from exc
    if not isinstance(raw, dict) or raw.get("schema_version") != CLAIM_SCHEMA:
        raise ClaimError(f"unsupported AutoDev claim schema on {ref}")
    claim = Claim(
        repository=str(raw.get("repository", "")),
        issue_number=int(raw.get("issue_number", 0) or 0),
        worker_id=_validate_worker_id(str(raw.get("worker_id", ""))),
        run_id=str(raw.get("run_id", "")),
        claim_id=str(raw.get("claim_id", "")),
        acquired_at=str(raw.get("acquired_at", "")),
        heartbeat_at=str(raw.get("heartbeat_at", "")),
        lease_seconds=int(raw.get("lease_seconds", 0) or 0),
        ref=ref,
        sha=sha,
    )
    if claim.issue_number <= 0 or claim.ref != claim_ref(claim.issue_number):
        raise ClaimError(f"claim issue/ref identity mismatch on {ref}")
    if not claim.repository or not claim.run_id or not claim.claim_id:
        raise ClaimError(f"claim metadata is incomplete on {ref}")
    if claim.lease_seconds <= 0:
        raise ClaimError(f"claim lease is invalid on {ref}")
    _parse_time(claim.acquired_at)
    _parse_time(claim.heartbeat_at)
    return claim


def _read_claim_from_ref(
    repo: Path,
    ref: str,
    sha: str,
    *,
    runner: Callable[..., object] = subprocess.run,
) -> Claim:
    _git(repo, ["fetch", "--quiet", "--no-tags", "origin", ref], runner=runner)
    shown = _git(repo, ["show", "-s", "--format=%B", sha], runner=runner)
    return _parse_claim_message(_stdout(shown), ref=ref, sha=sha)


def get_claim(
    repo: Path,
    issue_number: int,
    *,
    runner: Callable[..., object] = subprocess.run,
) -> Claim | None:
    ref = claim_ref(issue_number)
    sha = _remote_ref_sha(repo, ref, runner=runner)
    if not sha:
        return None
    return _read_claim_from_ref(repo, ref, sha, runner=runner)


def list_claims(
    repo: Path,
    *,
    runner: Callable[..., object] = subprocess.run,
) -> tuple[Claim, ...]:
    pattern = CLAIM_REF_PREFIX + "*"
    result = _git(repo, ["ls-remote", "--heads", "origin", pattern], runner=runner)
    pairs: list[tuple[str, str]] = []
    for line in _stdout(result).splitlines():
        fields = line.split()
        if len(fields) < 2 or not fields[1].startswith(CLAIM_REF_PREFIX):
            continue
        pairs.append((fields[1], fields[0]))
    claims = [
        _read_claim_from_ref(repo, ref, sha, runner=runner)
        for ref, sha in sorted(pairs)
    ]
    return tuple(sorted(claims, key=lambda item: item.issue_number))


def claim_expired(claim: Claim, *, now: datetime | None = None) -> bool:
    current = (now or _now()).astimezone(timezone.utc)
    heartbeat = _parse_time(claim.heartbeat_at)
    return current >= heartbeat + timedelta(seconds=claim.lease_seconds)


def _base_commit(repo: Path, base_ref: str, *, runner: Callable[..., object]) -> str:
    result = _git(repo, ["rev-parse", "--verify", base_ref], runner=runner)
    value = _stdout(result).strip()
    if not re.fullmatch(r"[0-9a-fA-F]{40,64}", value):
        raise ClaimError(f"could not resolve claim base ref: {base_ref}")
    return value


def _create_claim_commit(
    repo: Path,
    parent_sha: str,
    metadata: dict[str, object],
    *,
    runner: Callable[..., object],
) -> str:
    tree_result = _git(repo, ["rev-parse", f"{parent_sha}^{{tree}}"], runner=runner)
    tree = _stdout(tree_result).strip()
    args = [
        "-c",
        "user.name=AutoDev Claim",
        "-c",
        "user.email=autodev-claim@localhost",
        "commit-tree",
        tree,
        "-p",
        parent_sha,
    ]
    result = _git(
        repo,
        args,
        runner=runner,
        input_text=_claim_message(metadata),
    )
    sha = _stdout(result).strip()
    if not re.fullmatch(r"[0-9a-fA-F]{40,64}", sha):
        raise ClaimError("git commit-tree did not return a claim commit SHA")
    return sha


def _claim_metadata(
    *,
    github_repo: str,
    issue_number: int,
    worker_id: str,
    run_id: str,
    claim_id: str,
    acquired_at: str,
    heartbeat_at: str,
    lease_seconds: int,
) -> dict[str, object]:
    return {
        "schema_version": CLAIM_SCHEMA,
        "repository": github_repo,
        "issue_number": issue_number,
        "worker_id": worker_id,
        "run_id": run_id,
        "claim_id": claim_id,
        "acquired_at": acquired_at,
        "heartbeat_at": heartbeat_at,
        "lease_seconds": lease_seconds,
    }


def _push_with_lease(
    repo: Path,
    *,
    ref: str,
    new_sha: str,
    expected_sha: str,
    runner: Callable[..., object],
) -> bool:
    expected = expected_sha
    lease = f"--force-with-lease={ref}:{expected}"
    result = _git(
        repo,
        ["push", lease, "origin", f"{new_sha}:{ref}"],
        runner=runner,
        check=False,
    )
    if _returncode(result) == 0:
        return True
    if _is_push_race(result):
        return False
    _require_ok(result, ["git", "push", lease, "origin", f"{new_sha}:{ref}"])
    return False


def _delete_with_lease(
    repo: Path,
    claim: Claim,
    *,
    runner: Callable[..., object],
) -> bool:
    lease = f"--force-with-lease={claim.ref}:{claim.sha}"
    result = _git(
        repo,
        ["push", lease, "origin", f":{claim.ref}"],
        runner=runner,
        check=False,
    )
    if _returncode(result) == 0:
        return True
    if _is_push_race(result):
        return False
    _require_ok(result, ["git", "push", lease, "origin", f":{claim.ref}"])
    return False


def _new_claim(
    repo: Path,
    github_repo: str,
    issue_number: int,
    worker_id: str,
    base_ref: str,
    *,
    lease_minutes: int,
    runner: Callable[..., object],
    now: datetime,
) -> Claim:
    ref = claim_ref(issue_number)
    acquired = _iso(now)
    run_id = uuid.uuid4().hex
    claim_id = uuid.uuid4().hex
    metadata = _claim_metadata(
        github_repo=github_repo,
        issue_number=issue_number,
        worker_id=worker_id,
        run_id=run_id,
        claim_id=claim_id,
        acquired_at=acquired,
        heartbeat_at=acquired,
        lease_seconds=lease_minutes * 60,
    )
    parent = _base_commit(repo, base_ref, runner=runner)
    sha = _create_claim_commit(repo, parent, metadata, runner=runner)
    return Claim(
        repository=github_repo,
        issue_number=issue_number,
        worker_id=worker_id,
        run_id=run_id,
        claim_id=claim_id,
        acquired_at=acquired,
        heartbeat_at=acquired,
        lease_seconds=lease_minutes * 60,
        ref=ref,
        sha=sha,
    )


def recovery_evidence(
    repo: Path,
    github_repo: str,
    issue_number: int,
    *,
    runner: Callable[..., object] = subprocess.run,
) -> tuple[str, ...]:
    evidence: list[str] = []
    branch_pattern = f"refs/heads/autodev/issue-{issue_number}-*"
    branches = _git(repo, ["ls-remote", "--heads", "origin", branch_pattern], runner=runner)
    if _stdout(branches).strip():
        evidence.append("remote AutoDev issue branch exists")

    argv = [
        "gh",
        "pr",
        "list",
        "--repo",
        github_repo,
        "--state",
        "open",
        "--limit",
        "1000",
        "--json",
        "headRefName,url",
    ]
    result = _run(repo, argv, runner=runner)
    if _returncode(result) != 0:
        evidence.append("open-PR recovery check unavailable")
        return tuple(evidence)
    try:
        raw = json.loads(_stdout(result) or "[]")
    except json.JSONDecodeError:
        evidence.append("open-PR recovery check returned invalid JSON")
        return tuple(evidence)
    prefix = f"autodev/issue-{issue_number}-"
    if isinstance(raw, list) and any(
        isinstance(item, dict) and str(item.get("headRefName", "")).startswith(prefix)
        for item in raw
    ):
        evidence.append("open AutoDev PR exists")
    return tuple(evidence)


def _set_running_label(
    repo: Path,
    github_repo: str,
    issue_number: int,
    *,
    enabled: bool,
    runner: Callable[..., object],
) -> bool:
    action = "--add-label" if enabled else "--remove-label"
    argv = [
        "gh",
        "issue",
        "edit",
        str(issue_number),
        "--repo",
        github_repo,
        action,
        issue_queue.RUNNING_LABEL,
    ]
    result = _run(repo, argv, runner=runner)
    return _returncode(result) == 0


def reconcile_stale_claims(
    repo: Path,
    github_repo: str,
    *,
    runner: Callable[..., object] = subprocess.run,
    now: datetime | None = None,
    evidence_checker: Callable[[Path, str, int], tuple[str, ...]] | None = None,
) -> RecoveryResult:
    current = (now or _now()).astimezone(timezone.utc)
    recovered: list[int] = []
    protected: list[int] = []
    raced: list[int] = []
    checker = evidence_checker or (
        lambda path, repository, issue: recovery_evidence(
            path,
            repository,
            issue,
            runner=runner,
        )
    )
    for claim in list_claims(repo, runner=runner):
        if claim.repository != github_repo:
            raise ClaimError(
                f"claim repository identity mismatch on {claim.ref}: {claim.repository!r}"
            )
        if not claim_expired(claim, now=current):
            continue
        evidence = checker(repo, github_repo, claim.issue_number)
        if evidence:
            protected.append(claim.issue_number)
            continue
        if not _set_running_label(
            repo,
            github_repo,
            claim.issue_number,
            enabled=False,
            runner=runner,
        ):
            protected.append(claim.issue_number)
            continue
        if _delete_with_lease(repo, claim, runner=runner):
            recovered.append(claim.issue_number)
            continue
        # The old worker renewed/replaced the claim after our stale read. Restore
        # its durable running marker rather than making a live claim appear ready.
        _set_running_label(
            repo,
            github_repo,
            claim.issue_number,
            enabled=True,
            runner=runner,
        )
        raced.append(claim.issue_number)
    return RecoveryResult(
        recovered=tuple(sorted(recovered)),
        protected=tuple(sorted(protected)),
        raced=tuple(sorted(raced)),
    )


def acquire_claim(
    repo: Path,
    github_repo: str,
    issue_number: int,
    worker_id: str,
    base_ref: str,
    *,
    policy: ClaimPolicy | None = None,
    runner: Callable[..., object] = subprocess.run,
    now: datetime | None = None,
    evidence_checker: Callable[[Path, str, int], tuple[str, ...]] | None = None,
) -> ClaimAttempt:
    repo = repo.expanduser().resolve()
    worker_id = _validate_worker_id(worker_id)
    current = (now or _now()).astimezone(timezone.utc)
    claim_policy = policy or load_claim_policy(repo)
    existing = get_claim(repo, issue_number, runner=runner)
    if existing is not None:
        if existing.repository != github_repo:
            raise ClaimError(
                f"claim repository identity mismatch on {existing.ref}: {existing.repository!r}"
            )
        if not claim_expired(existing, now=current):
            if existing.worker_id == worker_id:
                renewed = renew_claim(repo, existing, runner=runner, now=current)
                if renewed is not None:
                    return ClaimAttempt("OWNED", claim=renewed, owner=renewed)
            return ClaimAttempt(
                "BUSY",
                owner=existing,
                detail=f"issue #{issue_number} is actively claimed by {existing.worker_id}",
            )
        checker = evidence_checker or (
            lambda path, repository, issue: recovery_evidence(
                path,
                repository,
                issue,
                runner=runner,
            )
        )
        evidence = checker(repo, github_repo, issue_number)
        if evidence:
            return ClaimAttempt(
                "STALE_PROTECTED",
                owner=existing,
                detail="; ".join(evidence),
            )
        if not _delete_with_lease(repo, existing, runner=runner):
            winner = get_claim(repo, issue_number, runner=runner)
            return ClaimAttempt(
                "BUSY",
                owner=winner,
                detail="stale-claim recovery race was won by another worker",
            )

    candidate = _new_claim(
        repo,
        github_repo,
        issue_number,
        worker_id,
        base_ref,
        lease_minutes=claim_policy.lease_minutes,
        runner=runner,
        now=current,
    )
    if _push_with_lease(
        repo,
        ref=candidate.ref,
        new_sha=candidate.sha,
        expected_sha="",
        runner=runner,
    ):
        return ClaimAttempt("ACQUIRED", claim=candidate, owner=candidate)
    winner = get_claim(repo, issue_number, runner=runner)
    return ClaimAttempt(
        "BUSY",
        owner=winner,
        detail="distributed claim race was won by another worker",
    )


def renew_claim(
    repo: Path,
    claim: Claim,
    *,
    runner: Callable[..., object] = subprocess.run,
    now: datetime | None = None,
) -> Claim | None:
    current = (now or _now()).astimezone(timezone.utc)
    metadata = _claim_metadata(
        github_repo=claim.repository,
        issue_number=claim.issue_number,
        worker_id=claim.worker_id,
        run_id=claim.run_id,
        claim_id=claim.claim_id,
        acquired_at=claim.acquired_at,
        heartbeat_at=_iso(current),
        lease_seconds=claim.lease_seconds,
    )
    sha = _create_claim_commit(repo, claim.sha, metadata, runner=runner)
    if not _push_with_lease(
        repo,
        ref=claim.ref,
        new_sha=sha,
        expected_sha=claim.sha,
        runner=runner,
    ):
        return None
    return replace(claim, heartbeat_at=_iso(current), sha=sha)


def release_claim(
    repo: Path,
    claim: Claim,
    *,
    runner: Callable[..., object] = subprocess.run,
) -> bool:
    current = get_claim(repo, claim.issue_number, runner=runner)
    if current is None:
        return True
    if current.worker_id != claim.worker_id or current.claim_id != claim.claim_id:
        return False
    return _delete_with_lease(repo, current, runner=runner)


def active_claims(
    repo: Path,
    *,
    runner: Callable[..., object] = subprocess.run,
    now: datetime | None = None,
    include_stale: bool = False,
) -> tuple[Claim, ...]:
    claims = list_claims(repo, runner=runner)
    if include_stale:
        return claims
    return tuple(item for item in claims if not claim_expired(item, now=now))


class HeartbeatLease:
    def __init__(
        self,
        repo: Path,
        claim: Claim,
        *,
        runner: Callable[..., object] = subprocess.run,
        interval_seconds: float | None = None,
    ) -> None:
        self.repo = repo.expanduser().resolve()
        self.claim = claim
        self.runner = runner
        self.interval_seconds = interval_seconds or max(
            30.0,
            min(300.0, claim.lease_seconds / 3.0),
        )
        self.lost = False
        self.error = ""
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._mutex = threading.Lock()

    def _loop(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            try:
                with self._mutex:
                    renewed = renew_claim(
                        self.repo,
                        self.claim,
                        runner=self.runner,
                    )
                    if renewed is None:
                        self.lost = True
                        self.error = "distributed claim ownership changed while the run was active"
                        self._stop.set()
                        return
                    self.claim = renewed
            except Exception as exc:
                # Do not immediately declare ownership lost for a transient network
                # failure. The lease timestamp remains authoritative; stale recovery
                # cannot take over until the published heartbeat actually expires.
                self.error = str(exc)

    def __enter__(self) -> "HeartbeatLease":
        self._thread = threading.Thread(
            target=self._loop,
            name=f"autodev-claim-{self.claim.issue_number}",
            daemon=True,
        )
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(1.0, min(5.0, self.interval_seconds)))
            self._thread = None

    def latest_claim(self) -> Claim:
        with self._mutex:
            return self.claim


def run_worker_cli(
    argv: list[str] | None = None,
    *,
    home: Path | None = None,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    parser = argparse.ArgumentParser(prog="autodev scheduler worker-id")
    parser.add_argument("--set", dest="worker_id", default="")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        identity = (
            set_worker_identity(args.worker_id, home=home)
            if args.worker_id
            else worker_identity(home=home)
        )
    except ClaimError as exc:
        print(str(exc), file=stderr)
        return 2
    payload = identity.to_json()
    print(
        json.dumps(payload, sort_keys=True)
        if args.json
        else f"AutoDev worker identity: {identity.worker_id}",
        file=stdout,
    )
    return 0
