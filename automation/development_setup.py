from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Callable

from automation import development_policy, queue_github


REPO_CONFIG = Path(".autodev") / "repo.json"
Diagnostic = tuple[str, str, str]


def requested_development(
    strategy: str,
    integration_branch: str,
    release_branch: str,
) -> dict[str, str] | None:
    strategy = str(strategy or "").strip().casefold()
    integration_branch = str(integration_branch or "").strip()
    release_branch = str(release_branch or "").strip()
    if not strategy:
        if integration_branch or release_branch:
            raise development_policy.DevelopmentPolicyError(
                "--integration-branch/--release-branch require --development-strategy"
            )
        return None

    raw: dict[str, str] = {"strategy": strategy}
    if strategy == development_policy.GIT_FLOW:
        raw["integration_branch"] = integration_branch or "develop"
        raw["release_branch"] = release_branch or "main"
    elif strategy == development_policy.TRUNK:
        branch = integration_branch or release_branch or "main"
        if integration_branch and release_branch and integration_branch != release_branch:
            raise development_policy.DevelopmentPolicyError(
                "trunk strategy requires the same integration and release branch"
            )
        raw["integration_branch"] = branch
        raw["release_branch"] = branch
    else:
        raw["integration_branch"] = integration_branch
        raw["release_branch"] = release_branch

    parsed = development_policy.parse_development_policy(
        raw,
        default_branch="main",
        source="repo install arguments",
    )
    return {
        "strategy": parsed.strategy,
        "integration_branch": parsed.integration_branch,
        "release_branch": parsed.release_branch,
    }


def policy_diagnostic(repo: Path) -> Diagnostic:
    try:
        policy = development_policy.load_development_policy(repo, default_branch="main")
    except development_policy.DevelopmentPolicyError as exc:
        return ("development-strategy", "error", str(exc))
    return (
        "development-strategy",
        "ok",
        development_policy.describe(policy),
    )


def _development_is_explicit(repo: Path) -> bool:
    path = repo / REPO_CONFIG
    if not path.is_file():
        return False
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(value, dict) and "development" in value


def branch_diagnostics(
    repo: Path,
    github_repo: str,
    *,
    runner: Callable[..., object] = subprocess.run,
) -> tuple[Diagnostic, ...]:
    """Check configured branch roles when the repository explicitly opted in.

    Legacy trunk repositories intentionally do not gain an extra GitHub API
    dependency merely because AutoDev learned about development strategies.
    Explicit strategy configuration, especially Git Flow, is checked strictly.
    """
    if not _development_is_explicit(repo):
        return ()
    try:
        policy = development_policy.load_development_policy(repo, default_branch="main")
    except development_policy.DevelopmentPolicyError as exc:
        return (("development-branches", "error", str(exc)),)

    diagnostics: list[Diagnostic] = []
    seen: set[str] = set()
    for role, branch in (
        ("integration", policy.integration_branch),
        ("release", policy.release_branch),
    ):
        if branch in seen:
            continue
        seen.add(branch)
        result = queue_github._run_gh(  # type: ignore[attr-defined]
            repo,
            ["api", f"repos/{github_repo}/git/ref/heads/{branch}"],
            runner=runner,
            check=False,
        )
        exists = int(getattr(result, "returncode", 1)) == 0
        diagnostics.append(
            (
                f"development-{role}-branch",
                "ok" if exists else "error",
                (
                    f"{branch} exists; normal AutoDev PR target={policy.normal_work_branch}"
                    if exists
                    else f"configured {role} branch {branch!r} does not exist; "
                    "create it from the intended trusted history before running AutoDev"
                ),
            )
        )
    return tuple(diagnostics)
