from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path


VALID_INTENTS = ("major", "minor", "patch", "none")
DEFAULT_INTENT = "patch"
REPO_CONFIG = Path(".autodev") / "repo.json"
INTENT_RE = re.compile(
    r"^\s*\+semver:\s*(major|minor|patch|none)\s*$",
    re.IGNORECASE | re.MULTILINE,
)


class SemVerIntentError(ValueError):
    pass


@dataclass(frozen=True)
class ResolvedSemVerIntent:
    intent: str
    source: str


def explicit_intents(text: str) -> list[str]:
    return [match.group(1).casefold() for match in INTENT_RE.finditer(str(text or ""))]


def normalize_intent(value: str, *, source: str) -> str:
    normalized = str(value or "").strip().casefold()
    if normalized not in VALID_INTENTS:
        raise SemVerIntentError(
            f"invalid {source} SemVer intent {value!r}; expected one of: "
            + ", ".join(VALID_INTENTS)
        )
    return normalized


def resolve_intent(
    issue_text: str,
    *,
    explicit: str = "",
    repository_default: str = "",
) -> ResolvedSemVerIntent:
    issue_values = explicit_intents(issue_text)
    if len(issue_values) > 1:
        raise SemVerIntentError(
            "source issue contains duplicate/conflicting +semver directives; "
            "keep exactly one +semver: major|minor|patch|none directive"
        )

    explicit_value = normalize_intent(explicit, source="explicit") if explicit.strip() else ""
    if issue_values:
        issue_value = issue_values[0]
        if explicit_value and explicit_value != issue_value:
            raise SemVerIntentError(
                f"explicit SemVer intent {explicit_value!r} conflicts with source issue "
                f"intent {issue_value!r}; the issue-owned directive is authoritative"
            )
        return ResolvedSemVerIntent(issue_value, "issue")

    if explicit_value:
        return ResolvedSemVerIntent(explicit_value, "explicit")

    if repository_default.strip():
        return ResolvedSemVerIntent(
            normalize_intent(repository_default, source="repository-default"),
            "repository-default",
        )

    return ResolvedSemVerIntent(DEFAULT_INTENT, "built-in-default")


def repository_default(repo: Path) -> str:
    path = repo.expanduser().resolve() / REPO_CONFIG
    if not path.is_file():
        return ""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SemVerIntentError(
            f"cannot resolve repository SemVer intent from invalid AutoDev config: {path}"
        ) from exc
    if not isinstance(value, dict):
        raise SemVerIntentError(
            f"AutoDev repository config must be a JSON object: {path}"
        )
    raw = value.get("default_semver_intent", "")
    if raw in (None, ""):
        return ""
    if not isinstance(raw, str):
        raise SemVerIntentError(
            "repository default_semver_intent must be a string"
        )
    return normalize_intent(raw, source="repository-default")


def without_directives(text: str) -> str:
    lines = str(text or "").splitlines()
    filtered = [line for line in lines if INTENT_RE.fullmatch(line) is None]
    return "\n".join(filtered).rstrip()


def directive(intent: str) -> str:
    return "+semver: " + normalize_intent(intent, source="resolved")
