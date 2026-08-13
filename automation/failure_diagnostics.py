from __future__ import annotations

import hashlib
import re
from pathlib import Path

FAILURE_PROVIDER_CAPABILITY = "provider-capability/request-too-large"
_LIMIT_REQUESTED = re.compile(
    r"(?:tpm|tokens? per minute|token limit).*?limit[:= ]+(?P<limit>[\d,]+).*?requested[:= ]+(?P<requested>[\d,]+)",
    re.IGNORECASE | re.DOTALL,
)
_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_ISO_TIMESTAMP = re.compile(r"\b\d{4}-\d{2}-\d{2}[T ][0-9:.+-]+Z?\b")
_DURATION = re.compile(r"\b\d+(?:\.\d+)?\s*(?:ms|milliseconds?|seconds?|secs?)\b", re.IGNORECASE)


def classify_provider_failure(text: str, fallback: str) -> str:
    lowered = text.casefold()
    if "request too large" in lowered or "request is too large" in lowered:
        return FAILURE_PROVIDER_CAPABILITY
    if "maximum context length" in lowered and "requested" in lowered:
        return FAILURE_PROVIDER_CAPABILITY
    match = _LIMIT_REQUESTED.search(text)
    if match is not None:
        try:
            limit = int(match.group("limit").replace(",", ""))
            requested = int(match.group("requested").replace(",", ""))
        except ValueError:
            return fallback
        if requested > limit:
            return FAILURE_PROVIDER_CAPABILITY
    return fallback


def normalize_failure_evidence(text: str, repo: Path | None = None) -> str:
    value = _ANSI.sub("", text)
    if repo is not None:
        value = value.replace(str(repo.resolve()), "<repo>")
    value = _ISO_TIMESTAMP.sub("<timestamp>", value)
    value = _DURATION.sub("<duration>", value)
    return " ".join(value.split())


def local_failure_fingerprint(command: str, evidence: str, repo: Path | None = None) -> str:
    normalized = normalize_failure_evidence(evidence, repo)
    if not command.strip() and not normalized:
        return ""
    return hashlib.sha256(
        f"local-check|{command.strip()}|{normalized}".encode("utf-8", errors="replace")
    ).hexdigest()
