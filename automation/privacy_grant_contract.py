from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path


STORE_VERSION = 1

STORE_ENV = "AUTODEV_PRIVACY_GRANTS_PATH"

REPOSITORY_ID_ENV = "AUTODEV_PRIVACY_REPOSITORY_ID"

DEFAULT_STORE = Path(".autodev") / "privacy-grants.json"

DURATION_DELTAS = {
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
}

DURATIONS = ("run", "24h", "7d", "30d", "until-revoked")

SCOPES = ("configured", "provider", "exact")

_BYPASS_DEPTH = 0
