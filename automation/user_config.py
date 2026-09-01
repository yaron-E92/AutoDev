from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from automation.opencode_adapter_contract import ROLE_NAMES


CONFIG_VERSION = 1
USER_CONFIG_ENV = "AUTODEV_USER_CONFIG"
_PROFILE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_REPOSITORY = re.compile(r"^[^/\s]+/[^/\s]+$")


class UserConfigError(ValueError):
    pass


def config_path() -> Path | None:
    explicit = os.environ.get(USER_CONFIG_ENV, "").strip()
    if explicit:
        return Path(explicit).expanduser()
    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
    if xdg:
        return Path(xdg).expanduser() / "autodev" / "config.json"
    appdata = os.environ.get("APPDATA", "").strip()
    if os.name == "nt" and appdata:
        return Path(appdata).expanduser() / "AutoDev" / "config.json"
    try:
        home = Path.home()
    except RuntimeError:
        return None
    return home / ".config" / "autodev" / "config.json"


def load(path: Path | None = None) -> dict[str, object]:
    resolved = path or config_path()
    if resolved is None or not resolved.is_file():
        return {}
    try:
        value = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise UserConfigError(f"cannot read AutoDev user configuration {resolved}: {exc}") from exc
    if not isinstance(value, dict):
        raise UserConfigError(f"AutoDev user configuration {resolved} must contain a JSON object")
    _validate(value, source=str(resolved))
    return value


def save(value: dict[str, object], path: Path | None = None) -> Path:
    resolved = path or config_path()
    if resolved is None:
        raise UserConfigError("cannot determine the AutoDev user configuration path")
    payload = dict(value)
    payload["version"] = CONFIG_VERSION
    _validate(payload, source=str(resolved))
    resolved.parent.mkdir(parents=True, exist_ok=True)
    temporary = resolved.with_name(resolved.name + ".tmp")
    try:
        temporary.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(resolved)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise UserConfigError(f"cannot write AutoDev user configuration {resolved}: {exc}") from exc
    return resolved


def profile_name(value: str) -> str:
    name = str(value or "").strip()
    if not name or not _PROFILE_NAME.fullmatch(name):
        raise UserConfigError(
            "model profile names must contain only letters, digits, '.', '_' or '-' and start with a letter or digit"
        )
    return name


def validate_model(value: str) -> str:
    model = str(value or "").strip()
    if not model or any(character.isspace() for character in model):
        raise UserConfigError("model routes must be non-empty provider/model identifiers without whitespace")
    provider, separator, model_id = model.partition("/")
    if not separator or not provider or not model_id or model_id.startswith("/"):
        raise UserConfigError(f"model route {model!r} must use provider/model syntax")
    return model


def set_model_profile(
    value: dict[str, object],
    name: str,
    models: dict[str, str],
) -> dict[str, object]:
    normalized = profile_name(name)
    if not models:
        raise UserConfigError("a model profile must configure at least one AutoDev role")
    allowed = set(ROLE_NAMES)
    unknown = sorted(set(models) - allowed)
    if unknown:
        raise UserConfigError("unknown AutoDev role(s): " + ", ".join(unknown))
    profiles = dict(_object(value.get("model_profiles"), "model_profiles"))
    profiles[normalized] = {role: validate_model(model) for role, model in sorted(models.items())}
    result = dict(value)
    result["model_profiles"] = profiles
    return result


def select_profile(
    value: dict[str, object],
    name: str,
    *,
    repository: str = "",
) -> dict[str, object]:
    normalized = profile_name(name)
    profiles = _object(value.get("model_profiles"), "model_profiles")
    if normalized not in profiles:
        raise UserConfigError(f"unknown model profile {normalized!r}")
    result = dict(value)
    if repository:
        repo = normalize_repository(repository)
        repositories = dict(_object(value.get("repositories"), "repositories"))
        repositories[repo] = {"model_profile": normalized}
        result["repositories"] = repositories
    else:
        result["active_model_profile"] = normalized
    return result


def clear_profile_selection(
    value: dict[str, object],
    *,
    repository: str = "",
) -> dict[str, object]:
    result = dict(value)
    if repository:
        repo = normalize_repository(repository)
        repositories = dict(_object(value.get("repositories"), "repositories"))
        for key in list(repositories):
            if str(key).casefold() == repo.casefold():
                repositories.pop(key, None)
        if repositories:
            result["repositories"] = repositories
        else:
            result.pop("repositories", None)
    else:
        result.pop("active_model_profile", None)
    return result


def normalize_repository(value: str) -> str:
    repo = str(value or "").strip().removesuffix(".git")
    if not _REPOSITORY.fullmatch(repo):
        raise UserConfigError(f"GitHub repository identity {value!r} must use OWNER/REPO syntax")
    return repo


def github_repository_from_remote(value: str) -> str:
    remote = str(value or "").strip()
    if not remote:
        return ""
    match = re.fullmatch(r"(?:[^@]+@)?github\.com:(.+)", remote, flags=re.IGNORECASE)
    if match:
        path = match.group(1)
    else:
        parsed = urlparse(remote)
        if parsed.hostname is None or parsed.hostname.casefold() != "github.com":
            return ""
        path = parsed.path.lstrip("/")
    path = path.removesuffix(".git").strip("/")
    try:
        return normalize_repository(path)
    except UserConfigError:
        return ""


def repository_identity(
    repo: Path,
    *,
    runner: Callable[..., object] = subprocess.run,
) -> str:
    try:
        completed = runner(
            ["git", "-C", str(repo.expanduser().resolve()), "remote", "get-url", "origin"],
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
    return github_repository_from_remote(str(getattr(completed, "stdout", "") or "").strip())


def effective_model_profile(
    repo: Path,
    *,
    value: dict[str, object] | None = None,
    runner: Callable[..., object] = subprocess.run,
) -> tuple[str, str, dict[str, str]]:
    config = load() if value is None else value
    profiles = _object(config.get("model_profiles"), "model_profiles")
    if not profiles:
        return "", "", {}

    selected = ""
    source = ""
    repositories = _object(config.get("repositories"), "repositories")
    if repositories:
        identity = repository_identity(repo, runner=runner)
        if identity:
            for key, raw in repositories.items():
                if str(key).casefold() != identity.casefold():
                    continue
                entry = _object(raw, f"repositories.{key}")
                selected = str(entry.get("model_profile", "") or "").strip()
                if selected:
                    source = f"repository:{identity}"
                break
    if not selected:
        selected = str(config.get("active_model_profile", "") or "").strip()
        if selected:
            source = "user"
    if not selected:
        return "", "", {}
    raw_profile = profiles.get(selected)
    if not isinstance(raw_profile, dict):
        raise UserConfigError(f"selected model profile {selected!r} does not exist")
    models = {str(role): validate_model(str(model)) for role, model in raw_profile.items()}
    return selected, source, models


def scheduler_cadence(value: dict[str, object] | None = None) -> int | None:
    config = load() if value is None else value
    scheduler = _object(config.get("scheduler"), "scheduler")
    raw = scheduler.get("cadence_minutes")
    if raw in (None, ""):
        return None
    if isinstance(raw, bool):
        raise UserConfigError("scheduler.cadence_minutes must be an integer")
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise UserConfigError("scheduler.cadence_minutes must be an integer") from exc


def set_scheduler_cadence(value: dict[str, object], cadence: int) -> dict[str, object]:
    result = dict(value)
    scheduler = dict(_object(value.get("scheduler"), "scheduler"))
    scheduler["cadence_minutes"] = int(cadence)
    result["scheduler"] = scheduler
    return result


def _validate(value: dict[str, object], *, source: str) -> None:
    version = value.get("version", CONFIG_VERSION)
    if isinstance(version, bool) or int(version) != CONFIG_VERSION:
        raise UserConfigError(f"unsupported AutoDev user configuration version in {source}: {version!r}")

    profiles = _object(value.get("model_profiles"), "model_profiles")
    allowed = set(ROLE_NAMES)
    for raw_name, raw_profile in profiles.items():
        name = profile_name(str(raw_name))
        profile = _object(raw_profile, f"model_profiles.{name}")
        unknown = sorted(set(map(str, profile)) - allowed)
        if unknown:
            raise UserConfigError(
                f"model profile {name!r} contains unknown AutoDev role(s): " + ", ".join(unknown)
            )
        for role, model in profile.items():
            validate_model(str(model))

    active = str(value.get("active_model_profile", "") or "").strip()
    if active and active not in profiles:
        raise UserConfigError(f"active_model_profile references unknown model profile {active!r}")

    repositories = _object(value.get("repositories"), "repositories")
    for raw_repo, raw_entry in repositories.items():
        repo = normalize_repository(str(raw_repo))
        entry = _object(raw_entry, f"repositories.{repo}")
        selected = str(entry.get("model_profile", "") or "").strip()
        if selected and selected not in profiles:
            raise UserConfigError(
                f"repository {repo} references unknown model profile {selected!r}"
            )

    scheduler_cadence(value)


def _object(value: object, label: str) -> dict[str, object]:
    if value in (None, ""):
        return {}
    if not isinstance(value, dict):
        raise UserConfigError(f"AutoDev user configuration field {label} must be an object")
    return value
