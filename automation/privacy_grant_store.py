from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

from automation import repository_identity as repository_identity_resolver
from automation.privacy_grant_contract import (
    DEFAULT_STORE,
    REPOSITORY_ID_ENV,
    STORE_ENV,
    STORE_VERSION,
)

def _now() -> datetime:
    return datetime.now(timezone.utc)

def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()

def _parse_time(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)

def _store_path() -> Path:
    explicit = os.environ.get(STORE_ENV, "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    return (Path.home() / DEFAULT_STORE).resolve()

def _load_store() -> dict[str, object]:
    path = _store_path()
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"schema_version": STORE_VERSION, "grants": []}
    if not isinstance(value, dict) or value.get("schema_version") != STORE_VERSION:
        return {"schema_version": STORE_VERSION, "grants": []}
    grants = value.get("grants", [])
    if not isinstance(grants, list):
        grants = []
    return {
        "schema_version": STORE_VERSION,
        "grants": [item for item in grants if isinstance(item, dict)],
    }

def _save_store(value: dict[str, object]) -> None:
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": STORE_VERSION,
        "updated_at": _iso(_now()),
        "grants": [
            item for item in value.get("grants", []) if isinstance(item, dict)
        ],
    }
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    try:
        os.chmod(temporary, 0o600)
    except OSError:
        pass
    temporary.replace(path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass

def _normalize_github_remote(value: str) -> str:
    remote = value.strip()
    if remote.startswith("git@github.com:"):
        path = remote.split(":", 1)[1]
    else:
        parsed = urlparse(remote)
        if (parsed.hostname or "").casefold() != "github.com":
            return ""
        path = parsed.path.lstrip("/")
    path = path.rstrip("/")
    if path.endswith(".git"):
        path = path[:-4]
    parts = path.split("/")
    if len(parts) != 2 or not all(parts):
        return ""
    return f"github:{parts[0].casefold()}/{parts[1].casefold()}"

def repository_identity(repo: Path, *, runner=subprocess.run) -> str:
    explicit = os.environ.get(REPOSITORY_ID_ENV, "").strip()
    if explicit:
        return explicit
    try:
        resolved = repository_identity_resolver.resolve_github_repository(
            repo,
            runner=runner,
            allow_gh_fallback=False,
        )
    except repository_identity_resolver.RepositoryIdentityError:
        digest = hashlib.sha256(
            str(repo.expanduser().resolve()).encode("utf-8")
        ).hexdigest()
        return f"path:{digest}"
    owner, name = repository_identity_resolver.split_github_repository(resolved)
    return f"github:{owner.casefold()}/{name.casefold()}"
