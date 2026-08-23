from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path


AUTODEV_ROOT = Path(__file__).resolve().parents[1]

CONFIG_PATH = Path(".autodev") / "windows-verification.json"

DEFAULT_CALLER_WORKFLOW = "autodev-windows-verification.yml"

REQUEST_FILE = "windows-verification-request.json"

RESULT_FILE = "windows-verification-result.json"

REPAIR_FILE = "windows-repair.md"

MANIFEST_STAGE = "windows-verified"

SCHEMA_VERSION = 1

DEFAULT_TIMEOUT_SECONDS = 3600

DEFAULT_POLL_SECONDS = 5.0

MAX_CAPTURE_CHARS = 24000

FAILURE_CODE_REPAIRABLE = "code-repairable"

FAILURE_TRANSIENT = "transient/retryable-infrastructure"

FAILURE_DETERMINISTIC = "non-retryable-deterministic"

_TRANSIENT_MARKERS = (
    "timed out",
    "timeout",
    "temporarily unavailable",
    "connection reset",
    "connection refused",
    "could not resolve host",
    "name resolution",
    "network is unreachable",
    "rate limit",
    "http 429",
    "http 500",
    "http 502",
    "http 503",
    "http 504",
    "service unavailable",
    "unable to load the service index",
    "the hosted runner",
    "runner has received a shutdown signal",
    "failed to download action",
    "unable to resolve action",
    "the operation was canceled",
)

_COMMAND_MARKER = "AUTODEV_WINDOWS_COMMAND_START="

_ACTIONS_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

class WindowsVerificationError(ValueError):
    pass

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
