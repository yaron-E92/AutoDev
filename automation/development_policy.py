from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping


REPO_CONFIG = Path(".autodev") / "repo.json"
TRUNK = "trunk"
GIT_FLOW = "git-flow"
SUPPORTED_STRATEGIES = {TRUNK, GIT_FLOW}


class DevelopmentPolicyError(RuntimeError):
    pass


@dataclass(frozen=True)
class DevelopmentPolicy:
    strategy: str
    integration_branch: str
    release_branch: str
    source: str

    @property
    def normal_work_branch(self) -> str:
        return self.integration_branch

    def to_json(self) -> dict[str, str]:
        return asdict(self)


_FORBIDDEN_REF_CHARS = re.compile(r"[\x00-\x20\x7f~^:?*\\\[]")


def validate_branch_name(value: str, *, field: str = "branch") -> str:
    branch = str(value or "").strip()
    if not branch:
        raise DevelopmentPolicyError(f"{field} must not be empty")
    if branch == "@" or branch.startswith("-"):
        raise DevelopmentPolicyError(f"invalid {field}: {branch!r}")
    if _FORBIDDEN_REF_CHARS.search(branch):
        raise DevelopmentPolicyError(f"invalid {field}: {branch!r}")
    if branch.startswith("/") or branch.endswith("/") or branch.endswith("."):
        raise DevelopmentPolicyError(f"invalid {field}: {branch!r}")
    if "//" in branch or ".." in branch or "@{" in branch:
        raise DevelopmentPolicyError(f"invalid {field}: {branch!r}")
    for component in branch.split("/"):
        if not component or component.startswith(".") or component.endswith(".lock"):
            raise DevelopmentPolicyError(f"invalid {field}: {branch!r}")
    return branch


def parse_development_policy(
    raw: object,
    *,
    default_branch: str = "main",
    source: str = "repository policy",
) -> DevelopmentPolicy:
    default = validate_branch_name(default_branch, field="repository default branch")
    if raw is None:
        return DevelopmentPolicy(TRUNK, default, default, "built-in trunk default")
    if not isinstance(raw, dict):
        raise DevelopmentPolicyError(f"development in {source} must be an object")

    strategy = str(raw.get("strategy", TRUNK) or TRUNK).strip().casefold()
    if strategy not in SUPPORTED_STRATEGIES:
        raise DevelopmentPolicyError(
            f"unsupported development strategy in {source}: {strategy!r}; "
            f"expected one of {', '.join(sorted(SUPPORTED_STRATEGIES))}"
        )

    if strategy == TRUNK:
        integration = validate_branch_name(
            str(raw.get("integration_branch", default) or default),
            field="development.integration_branch",
        )
        release = validate_branch_name(
            str(raw.get("release_branch", default) or default),
            field="development.release_branch",
        )
        if integration != release:
            raise DevelopmentPolicyError(
                "trunk strategy requires integration_branch and release_branch to resolve to the same branch"
            )
        return DevelopmentPolicy(TRUNK, integration, release, source)

    if "integration_branch" not in raw or "release_branch" not in raw:
        raise DevelopmentPolicyError(
            "git-flow strategy requires development.integration_branch and development.release_branch"
        )
    integration = validate_branch_name(
        str(raw.get("integration_branch", "")),
        field="development.integration_branch",
    )
    release = validate_branch_name(
        str(raw.get("release_branch", "")),
        field="development.release_branch",
    )
    if integration == release:
        raise DevelopmentPolicyError(
            "git-flow integration_branch and release_branch must be different"
        )
    return DevelopmentPolicy(GIT_FLOW, integration, release, source)


def load_development_policy(
    repo: Path,
    *,
    default_branch: str = "main",
) -> DevelopmentPolicy:
    path = repo / REPO_CONFIG
    if not path.is_file():
        return parse_development_policy(None, default_branch=default_branch)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DevelopmentPolicyError(f"invalid AutoDev repository config: {path}") from exc
    if not isinstance(raw, dict):
        raise DevelopmentPolicyError(f"AutoDev repository config must be a JSON object: {path}")
    return parse_development_policy(
        raw.get("development"),
        default_branch=default_branch,
        source=str(path),
    )


def normal_work_branch(
    repo: Path,
    *,
    default_branch: str = "main",
    explicit: str = "",
) -> str:
    override = str(explicit or "").strip()
    if override:
        return validate_branch_name(override, field="explicit base branch")
    return load_development_policy(repo, default_branch=default_branch).normal_work_branch


def assert_resume_compatible(
    repo: Path,
    state: Mapping[str, object],
    *,
    default_branch: str = "main",
) -> DevelopmentPolicy:
    """Refuse to reinterpret an already-prepared run after branch policy changes."""
    policy = load_development_policy(repo, default_branch=default_branch)
    persisted_base = str(state.get("Base", "") or "").strip()
    persisted_strategy = str(state.get("DevelopmentStrategy", "") or "").strip()
    persisted_integration = str(state.get("IntegrationBranch", "") or "").strip()
    persisted_release = str(state.get("ReleaseBranch", "") or "").strip()

    mismatches: list[str] = []
    if persisted_base and persisted_base != policy.normal_work_branch:
        mismatches.append(
            f"prepared base={persisted_base!r}, effective normal-work branch={policy.normal_work_branch!r}"
        )
    if persisted_strategy and persisted_strategy != policy.strategy:
        mismatches.append(
            f"prepared strategy={persisted_strategy!r}, effective strategy={policy.strategy!r}"
        )
    if persisted_integration and persisted_integration != policy.integration_branch:
        mismatches.append(
            f"prepared integration={persisted_integration!r}, effective integration={policy.integration_branch!r}"
        )
    if persisted_release and persisted_release != policy.release_branch:
        mismatches.append(
            f"prepared release={persisted_release!r}, effective release={policy.release_branch!r}"
        )
    if mismatches:
        raise DevelopmentPolicyError(
            "active AutoDev run is incompatible with the current repository development strategy: "
            + "; ".join(mismatches)
            + ". Finish/recover the prepared run under its original policy or explicitly restart it after inspecting existing branch/PR state."
        )
    return policy


def describe(policy: DevelopmentPolicy) -> str:
    return (
        f"strategy={policy.strategy} integration={policy.integration_branch} "
        f"release={policy.release_branch} normal-pr-target={policy.normal_work_branch}"
    )
