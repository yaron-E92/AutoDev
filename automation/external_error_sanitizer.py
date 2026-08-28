from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


REDACTED = "<redacted>"
TRUNCATION_MARKER = "... <truncated>"
DEFAULT_MAX_CHARS = 1600
DEFAULT_MAX_LINES = 12

_ANSI_ESCAPE_RE = re.compile(
    r"(?:\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07]*(?:\x07|\x1b\\)|\x1b[@-Z\\-_])"
)
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_URL_RE = re.compile(r"https?://[^\s<>'\"]+", re.IGNORECASE)
_HEADER_MAP_RE = re.compile(
    r"(?is)\b(?:response\.)?headers\s*[:=]\s*(\{.*?\}|\[.*?\])"
)
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_GITHUB_TOKEN_RE = re.compile(
    r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"
)

_SENSITIVE_QUERY_NAMES = {
    "access_token",
    "api_key",
    "apikey",
    "auth",
    "authorization",
    "code",
    "credential",
    "key",
    "signature",
    "sig",
    "signed",
    "token",
    "x-amz-credential",
    "x-amz-security-token",
    "x-amz-signature",
    "x-goog-signature",
}

_HEADER_VALUE_RE = re.compile(
    r"""(?ix)
    (?P<prefix>
        (?P<quote>["']?)
        (?P<name>
            authorization
            |proxy-authorization
            |cookie
            |set-cookie
            |x-api-key
            |x-codex-turn-state
        )
        (?P=quote)
        \s*[:=]\s*
    )
    (?P<value>
        ["'][^"'\r\n]*["']
        |[^,;\r\n}\]]+
    )
    """
)
_GENERIC_SECRET_ASSIGNMENT_RE = re.compile(
    r"""(?ix)
    \b(?P<name>
        api[_-]?key
        |access[_-]?token
        |refresh[_-]?token
        |token
        |secret
        |password
        |credential
    )\b
    \s*[:=]\s*
    (?P<value>
        ["'][^"'\r\n]*["']
        |[^\s,;\r\n}\]]+
    )
    """
)


@dataclass(frozen=True)
class SafeExternalError:
    category: str
    message: str
    role: str = ""
    runtime: str = ""
    phase: str = ""
    returncode: int | None = None
    retry_classification: str = ""
    termination: str = ""

    def to_json(self) -> dict[str, object]:
        # Explicit allowlist: no raw provider/response object is accepted here.
        return asdict(self)


def sanitize_external_text(
    value: object,
    *,
    max_chars: int = DEFAULT_MAX_CHARS,
    max_lines: int = DEFAULT_MAX_LINES,
) -> str:
    if isinstance(value, bytes):
        text = value.decode("utf-8", errors="replace")
    else:
        text = str(value or "")

    text = _ANSI_ESCAPE_RE.sub("", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _CONTROL_RE.sub(" ", text)

    # Whole header maps are never useful enough to justify persistence. This
    # also catches unknown provider/account headers without maintaining a
    # provider-specific denylist.
    text = _HEADER_MAP_RE.sub(f"headers={REDACTED}", text)
    text = _HEADER_VALUE_RE.sub(
        lambda match: f"{match.group('name')}={REDACTED}",
        text,
    )
    text = _BEARER_RE.sub(f"Bearer {REDACTED}", text)
    text = _GITHUB_TOKEN_RE.sub(REDACTED, text)
    text = _GENERIC_SECRET_ASSIGNMENT_RE.sub(
        lambda match: f"{match.group('name')}={REDACTED}",
        text,
    )
    text = _URL_RE.sub(_sanitize_url_match, text)

    lines = [line.rstrip() for line in text.split("\n")]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()

    truncated = False
    if max_lines > 0 and len(lines) > max_lines:
        keep_head = max(1, max_lines // 2)
        keep_tail = max(1, max_lines - keep_head - 1)
        lines = lines[:keep_head] + [TRUNCATION_MARKER] + lines[-keep_tail:]
        truncated = True

    text = "\n".join(lines)
    if max_chars > 0 and len(text) > max_chars:
        room = max(0, max_chars - len(TRUNCATION_MARKER) - 1)
        text = text[:room].rstrip() + "\n" + TRUNCATION_MARKER
        truncated = True

    if truncated and TRUNCATION_MARKER not in text and max_chars > len(TRUNCATION_MARKER):
        text = text.rstrip() + "\n" + TRUNCATION_MARKER
    return text.strip()


def safe_external_error(
    *,
    category: str,
    message: object,
    role: str = "",
    runtime: str = "",
    phase: str = "",
    returncode: int | None = None,
    retry_classification: str = "",
    termination: str = "",
) -> SafeExternalError:
    return SafeExternalError(
        category=_safe_atom(category),
        message=sanitize_external_text(message),
        role=_safe_atom(role),
        runtime=_safe_atom(runtime),
        phase=_safe_atom(phase),
        returncode=returncode if isinstance(returncode, int) else None,
        retry_classification=_safe_atom(retry_classification),
        termination=_safe_atom(termination),
    )


def _safe_atom(value: object) -> str:
    atom = re.sub(r"[^A-Za-z0-9._:/-]+", "-", str(value or "").strip())
    return atom[:120]


def _sanitize_url_match(match: re.Match[str]) -> str:
    raw = match.group(0)
    trailing = ""
    while raw and raw[-1] in ").,;]}":
        trailing = raw[-1] + trailing
        raw = raw[:-1]
    try:
        parts = urlsplit(raw)
    except ValueError:
        return raw + trailing
    if not parts.query:
        return raw + trailing
    changed = False
    query: list[tuple[str, str]] = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        lowered = key.casefold()
        sensitive = (
            lowered in _SENSITIVE_QUERY_NAMES
            or "token" in lowered
            or "signature" in lowered
            or lowered.endswith("_sig")
            or lowered.endswith("-sig")
        )
        if sensitive:
            query.append((key, REDACTED))
            changed = True
        else:
            query.append((key, value))
    if not changed:
        return raw + trailing
    sanitized = urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            parts.path,
            urlencode(query, doseq=True),
            parts.fragment,
        )
    )
    return sanitized + trailing
