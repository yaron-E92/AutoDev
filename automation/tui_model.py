from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
import shutil
import subprocess

from automation import (
    privacy,
    privacy_grant_commands,
    queue_presentation,
    queue_selection,
    queue_workflow,
    repository_identity,
    run_manifest,
    scheduler,
    scheduler_health_storage,
    ux_multimodal_resume,
    workflow_stages,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class TuiIssue:
    number: int
    title: str
    url: str
    queue_state: str
    blockers: tuple[str, ...] = ()


@dataclass(frozen=True)
class TuiRun:
    state: str = "NONE"
    issue_number: int = 0
    issue_title: str = ""
    status: str = ""
    branch: str = ""
    pr_url: str = ""
    pr_number: int = 0
    next_stage: str = ""
    next_action: str = ""
    resumable: bool = False
    local_check_passed: bool = False
    semantic_verified: bool = False
    verification_identity: str = ""
    multimodal_status: str = ""
    multimodal_checkpoint_valid: bool = False
    multimodal_evidence_identity: str = ""
    multimodal_targets: tuple[str, ...] = ()
    multimodal_violations: int = 0
    multimodal_unverifiable: int = 0
    multimodal_runtime: str = ""
    multimodal_model: str = ""
    multimodal_capability: str = ""
    non_success_summary: str = ""


@dataclass(frozen=True)
class TuiScheduler:
    state: str = "NOT_INSTALLED"
    backend: str = ""
    backend_state: str = ""
    role_runtime: str = ""
    runtime_fingerprint: str = ""
    cadence_minutes: int = 0
    last_run_state: str = ""
    last_run_at: str = ""
    notifications: str = "off"


@dataclass(frozen=True)
class TuiPrivacy:
    enabled: bool = False
    local_only: bool = False
    consent_mode: str = ""
    active_grants: int = 0
    expired_grants: int = 0
    revoked_grants: int = 0


@dataclass(frozen=True)
class TuiSnapshot:
    repository: str
    repo_path: str
    observed_at: str
    remote_observed_at: str = ""
    queue: dict[str, int] = field(default_factory=dict)
    issues: tuple[TuiIssue, ...] = ()
    selected_issue_number: int = 0
    active_claims: int = 0
    run: TuiRun = field(default_factory=TuiRun)
    scheduler: TuiScheduler = field(default_factory=TuiScheduler)
    privacy: TuiPrivacy = field(default_factory=TuiPrivacy)
    remote_error: str = ""

    def issue(self, number: int) -> TuiIssue | None:
        return next((item for item in self.issues if item.number == number), None)


class TuiModelError(RuntimeError):
    pass


def resolve_repository(
    repo: Path,
    *,
    github_repo: str = "",
    runner: Callable[..., object] = subprocess.run,
) -> tuple[Path, str]:
    root = repo.expanduser().resolve()
    if not root.is_dir() or not (root / ".git").exists():
        raise TuiModelError(f"not a Git repository root: {root}")
    try:
        identity = repository_identity.resolve_github_repository(
            root,
            explicit=github_repo,
            runner=runner,
            allow_gh_fallback=False,
        )
    except repository_identity.RepositoryIdentityError as exc:
        raise TuiModelError(str(exc)) from exc
    return root, identity


def collect_local(
    repo: Path,
    github_repo: str,
    *,
    home: Path | None = None,
    runner: Callable[..., object] = subprocess.run,
) -> TuiSnapshot:
    root = repo.expanduser().resolve()
    existing = queue_selection.inspect_existing_run(root)
    current = root / workflow_stages.CURRENT_DIR
    state: dict[str, object] = {}
    if current.is_dir():
        try:
            state = workflow_stages.read_state(current)
        except Exception:
            state = {}

    manifest: dict[str, object] = {}
    manifest_path = current / run_manifest.MANIFEST_NAME
    if manifest_path.is_file():
        try:
            manifest = run_manifest.load_manifest(manifest_path)
        except Exception:
            manifest = {}
    multimodal = ux_multimodal_resume.summary(current) if current.is_dir() else {}
    multimodal_targets = multimodal.get("targets", []) if isinstance(multimodal, dict) else []
    if not isinstance(multimodal_targets, list):
        multimodal_targets = []
    semantic_checkpoint = (
        run_manifest.stage_completed(manifest, "semantic-verified")
        if manifest
        else bool(
            state.get("SemanticVerified")
            or state.get("SemanticVerificationPassed")
            or state.get("VerificationProofVersion")
        )
    )

    run = TuiRun(
        state=existing.state,
        issue_number=int(state.get("IssueNumber", 0) or existing.issue_number or 0),
        issue_title=str(state.get("IssueTitle", "") or ""),
        status=str(state.get("Status", "") or ""),
        branch=str(state.get("BranchName", "") or ""),
        pr_url=str(state.get("PrUrl", "") or ""),
        pr_number=int(state.get("PrNumber", 0) or 0),
        next_stage=existing.next_stage,
        next_action=existing.next_action,
        resumable=existing.state == "RESUME_EXISTING",
        local_check_passed=bool(state.get("LastLocalCheckPassed")),
        semantic_verified=semantic_checkpoint,
        verification_identity=str(state.get("VerifiedSourceIdentity", "") or ""),
        multimodal_status=str(multimodal.get("status", "") or "") if isinstance(multimodal, dict) else "",
        multimodal_checkpoint_valid=bool(multimodal.get("present")) and semantic_checkpoint
        if isinstance(multimodal, dict)
        else False,
        multimodal_evidence_identity=str(multimodal.get("evidence_identity", "") or "")
        if isinstance(multimodal, dict)
        else "",
        multimodal_targets=tuple(str(value) for value in multimodal_targets),
        multimodal_violations=int(multimodal.get("violations", 0) or 0)
        if isinstance(multimodal, dict)
        else 0,
        multimodal_unverifiable=int(multimodal.get("unverifiable", 0) or 0)
        if isinstance(multimodal, dict)
        else 0,
        multimodal_runtime=str(multimodal.get("runtime", "") or "")
        if isinstance(multimodal, dict)
        else "",
        multimodal_model=str(multimodal.get("model", "") or "")
        if isinstance(multimodal, dict)
        else "",
        multimodal_capability=str(multimodal.get("capability", "") or "")
        if isinstance(multimodal, dict)
        else "",
        non_success_summary=_non_success_summary(current),
    )

    scheduler_state = TuiScheduler()
    try:
        status = scheduler.scheduler_status(root, github_repo=github_repo, home=home, runner=runner)
        registration_path = scheduler.registration_path(github_repo, home=home)
        loaded = scheduler._load_registration(registration_path)  # type: ignore[attr-defined]
        notifications = "off"
        if loaded is not None:
            try:
                notifications = scheduler_health_storage.load_notification_policy(
                    registration_path
                ).backend
            except Exception:
                notifications = "unknown"
        last_run = loaded.last_run if loaded and isinstance(loaded.last_run, dict) else {}
        scheduler_state = TuiScheduler(
            state=status.state,
            backend=status.backend,
            backend_state=status.backend_state,
            role_runtime=status.role_runtime,
            runtime_fingerprint=status.runtime_fingerprint,
            cadence_minutes=status.cadence_minutes,
            last_run_state=str(last_run.get("state", "") or ""),
            last_run_at=str(
                last_run.get("completed_at", "")
                or last_run.get("started_at", "")
                or last_run.get("observed_at", "")
                or ""
            ),
            notifications=notifications,
        )
    except Exception:
        # TUI observation is best-effort; canonical scheduler commands retain the
        # actionable error path. A broken scheduler must not make the TUI mutate or
        # crash an active run.
        scheduler_state = TuiScheduler(state="NEEDS_ATTENTION")

    policy = privacy.load_policy(root)
    grant_counts = {"active": 0, "expired": 0, "revoked": 0}
    try:
        for record in privacy_grant_commands.current_grants(root):
            value = str(record.get("status", ""))
            if value in grant_counts:
                grant_counts[value] += 1
    except Exception:
        pass
    privacy_state = TuiPrivacy(
        enabled=policy.enabled,
        local_only=policy.local_only,
        consent_mode=policy.consent_mode,
        active_grants=grant_counts["active"],
        expired_grants=grant_counts["expired"],
        revoked_grants=grant_counts["revoked"],
    )

    return TuiSnapshot(
        repository=github_repo,
        repo_path=str(root),
        observed_at=_now(),
        run=run,
        scheduler=scheduler_state,
        privacy=privacy_state,
    )


def collect_remote(
    snapshot: TuiSnapshot,
    *,
    runner: Callable[..., object] = subprocess.run,
) -> TuiSnapshot:
    root = Path(snapshot.repo_path)
    try:
        states = queue_workflow.inspect_queue(root, snapshot.repository, runner=runner)
        queue = queue_presentation.queue_summary(states)
        issues = tuple(
            TuiIssue(
                number=item.issue.number,
                title=item.issue.title,
                url=item.issue.url,
                queue_state=item.reason,
                blockers=tuple(
                    f"#{blocker.number} {blocker.title}" for blocker in item.open_blockers
                ),
            )
            for item in sorted(states, key=lambda value: value.issue.number)
            if item.issue.state == "open"
        )
        ready = [item.number for item in issues if item.queue_state == "ready"]
        selected = min(ready, default=0)
        if snapshot.run.issue_number and snapshot.run.issue_number in {
            item.number for item in issues
        }:
            selected = snapshot.run.issue_number
        run = snapshot.run
        matching = next((item for item in issues if item.number == run.issue_number), None)
        if matching is not None and not run.issue_title:
            run = replace(run, issue_title=matching.title)
        return replace(
            snapshot,
            observed_at=_now(),
            remote_observed_at=_now(),
            queue=dict(queue),
            issues=issues,
            selected_issue_number=selected,
            active_claims=int(queue.get("running", 0)),
            run=run,
            remote_error="",
        )
    except Exception as exc:
        return replace(
            snapshot,
            observed_at=_now(),
            remote_observed_at=_now(),
            remote_error=str(exc),
        )


def refresh_local(
    snapshot: TuiSnapshot,
    *,
    home: Path | None = None,
    runner: Callable[..., object] = subprocess.run,
) -> TuiSnapshot:
    refreshed = collect_local(
        Path(snapshot.repo_path),
        snapshot.repository,
        home=home,
        runner=runner,
    )
    return replace(
        refreshed,
        remote_observed_at=snapshot.remote_observed_at,
        queue=dict(snapshot.queue),
        issues=snapshot.issues,
        selected_issue_number=snapshot.selected_issue_number,
        active_claims=snapshot.active_claims,
        remote_error=snapshot.remote_error,
    )


def _non_success_summary(current: Path) -> str:
    path = current / "non-success-report.md"
    if not path.is_file():
        return ""
    try:
        lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()]
    except OSError:
        return ""
    useful = [
        line.removeprefix("- ").strip()
        for line in lines
        if line.startswith("- ") and line.strip() != "-"
    ]
    return " | ".join(useful[:3])[:600]
