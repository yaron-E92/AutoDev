from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Callable, Mapping
from urllib.parse import urlsplit


REPO_CONFIG = Path(".autodev") / "repo.json"
_GITHUB_COMPONENT = re.compile(r"^[A-Za-z0-9_.-]+$")


class RepositoryIdentityError(RuntimeError):
    pass


def _validate_component(value: str, label: str) -> str:
    result = value.strip()
    if not result or not _GITHUB_COMPONENT.fullmatch(result):
        raise RepositoryIdentityError(f"invalid GitHub {label}: {value!r}")
    return result


def split_github_repository(value: str, *, label: str = "repository") -> tuple[str, str]:
    normalized = value.strip()
    if normalized.count("/") != 1:
        raise RepositoryIdentityError(f"{label} must use owner/name format")
    owner, name = normalized.split("/", 1)
    return _validate_component(owner, "owner"), _validate_component(name, "repository name")


def github_repository_from_remote(remote_url: str) -> str | None:
    value = remote_url.strip()
    if not value:
        return None

    scp = re.fullmatch(r"git@github\.com:([^/]+)/([^/]+?)(?:\.git)?/?", value)
    if scp:
        try:
            owner = _validate_component(scp.group(1), "owner")
            name = _validate_component(scp.group(2), "repository name")
        except RepositoryIdentityError:
            return None
        return f"{owner}/{name}"

    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https", "ssh"} or parsed.hostname != "github.com":
        return None
    if parsed.query or parsed.fragment:
        return None
    if parsed.scheme == "ssh" and parsed.username not in {None, "git"}:
        return None

    path = parsed.path.strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    if path.count("/") != 1:
        return None
    try:
        owner, name = split_github_repository(path, label="GitHub remote")
    except RepositoryIdentityError:
        return None
    return f"{owner}/{name}"


def _configured_repository(repo: Path) -> str:
    path = repo / REPO_CONFIG
    if not path.is_file():
        return ""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RepositoryIdentityError(
            f"cannot read valid AutoDev repository configuration from {path}"
        ) from exc
    if not isinstance(value, dict):
        raise RepositoryIdentityError(f"AutoDev repository configuration must be an object: {path}")
    configured = value.get("github_repository", "")
    if configured in {None, ""}:
        return ""
    if not isinstance(configured, str):
        raise RepositoryIdentityError(
            f"AutoDev repository configuration field 'github_repository' must be owner/name: {path}"
        )
    owner, name = split_github_repository(
        configured,
        label="AutoDev repository configuration field 'github_repository'",
    )
    return f"{owner}/{name}"


def _remote_repository(
    repo: Path,
    remote_name: str,
    *,
    runner: Callable[..., object],
) -> tuple[str, bool]:
    try:
        completed = runner(
            ["git", "remote", "get-url", "--all", remote_name],
            cwd=repo,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
        )
    except OSError:
        return "", False

    if int(getattr(completed, "returncode", 1)) != 0:
        return "", False

    urls = [line.strip() for line in str(getattr(completed, "stdout", "") or "").splitlines() if line.strip()]
    if not urls:
        raise RepositoryIdentityError(f"Git remote {remote_name!r} has no configured URL")

    parsed = [github_repository_from_remote(url) for url in urls]
    if any(value is None for value in parsed):
        raise RepositoryIdentityError(
            f"Git remote {remote_name!r} is not an unambiguous supported GitHub HTTPS/SSH remote"
        )
    repositories = {str(value) for value in parsed}
    if len(repositories) != 1:
        raise RepositoryIdentityError(
            f"Git remote {remote_name!r} points to multiple GitHub repositories: {', '.join(sorted(repositories))}"
        )
    return next(iter(repositories)), True


def _legacy_gh_repository(repo: Path, *, runner: Callable[..., object]) -> str:
    try:
        completed = runner(
            ["gh", "repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"],
            cwd=repo,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
        )
    except OSError:
        return ""
    if int(getattr(completed, "returncode", 1)) != 0:
        return ""
    value = str(getattr(completed, "stdout", "") or "").strip()
    try:
        owner, name = split_github_repository(value, label="GitHub CLI repository identity")
    except RepositoryIdentityError:
        return ""
    return f"{owner}/{name}"


def resolve_github_repository(
    repo: Path,
    *,
    explicit: str = "",
    env: Mapping[str, str] | None = None,
    runner: Callable[..., object] = subprocess.run,
    allow_gh_fallback: bool = False,
) -> str:
    target = repo.expanduser().resolve()
    if explicit.strip():
        owner, name = split_github_repository(explicit, label="GitHub repository override")
        return f"{owner}/{name}"

    values = os.environ if env is None else env
    owner_override = values.get("GITHUB_OWNER", "").strip()
    name_override = values.get("GITHUB_REPO", "").strip()
    if owner_override:
        owner_override = _validate_component(owner_override, "owner")
    if name_override:
        name_override = _validate_component(name_override, "repository name")
    if owner_override and name_override:
        return f"{owner_override}/{name_override}"

    configured = _configured_repository(target)
    if configured:
        owner, name = split_github_repository(configured)
        return f"{owner_override or owner}/{name_override or name}"

    remote_name = values.get("REMOTE_NAME", "").strip() or "origin"
    remote_repository, remote_available = _remote_repository(
        target,
        remote_name,
        runner=runner,
    )
    if remote_repository:
        owner, name = split_github_repository(remote_repository)
        return f"{owner_override or owner}/{name_override or name}"

    if allow_gh_fallback and not remote_available:
        fallback = _legacy_gh_repository(target, runner=runner)
        if fallback:
            owner, name = split_github_repository(fallback)
            return f"{owner_override or owner}/{name_override or name}"

    raise RepositoryIdentityError(
        "could not resolve GitHub repository identity for "
        f"{target}; set GITHUB_OWNER/GITHUB_REPO, configure "
        f"{REPO_CONFIG.as_posix()} field 'github_repository', or configure a supported GitHub "
        f"HTTPS/SSH remote named {remote_name!r} (override with REMOTE_NAME)"
    )
