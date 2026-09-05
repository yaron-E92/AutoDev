from __future__ import annotations

from automation import claim_liveness, queue_contract

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from automation.claim_contract import (
    CLAIM_LIVENESS_STALLED,
    ClaimError,
    RecoveryResult,
    _now,
)
from automation.claim_process import (
    _git,
    _returncode,
    _run,
    _stdout,
)
from automation.claim_repository import (
    _delete_with_lease,
    claim_expired,
    list_claims,
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
        queue_contract.RUNNING_LABEL,
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

        if claim.liveness_state == CLAIM_LIVENESS_STALLED:
            try:
                snapshot = claim_liveness.progress_snapshot(
                    repo,
                    claim.issue_number,
                    runner=runner,
                )
            except Exception:
                protected.append(claim.issue_number)
                continue
            if not snapshot.terminal:
                protected.append(claim.issue_number)
                continue
            if _delete_with_lease(repo, claim, runner=runner):
                recovered.append(claim.issue_number)
            else:
                raced.append(claim.issue_number)
            continue

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
