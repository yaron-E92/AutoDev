from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


REPO_CONFIG = Path(".autodev") / "repo.json"


class UXPolicyError(ValueError):
    pass


@dataclass(frozen=True)
class UXRepositoryPolicy:
    enabled: bool = False
    artifact: str = ""
    product: str = ""

    @property
    def configured(self) -> bool:
        return self.enabled and bool(self.artifact)


def parse_ux_policy(value: object, *, source: str = ".autodev/repo.json") -> UXRepositoryPolicy:
    if value in (None, ""):
        return UXRepositoryPolicy()
    if not isinstance(value, dict):
        raise UXPolicyError(f"ux in {source} must be a JSON object")
    enabled = value.get("enabled", False)
    if not isinstance(enabled, bool):
        raise UXPolicyError(f"ux.enabled in {source} must be a boolean")
    artifact = value.get("artifact", "")
    product = value.get("product", "")
    if not isinstance(artifact, str):
        raise UXPolicyError(f"ux.artifact in {source} must be a string")
    if not isinstance(product, str):
        raise UXPolicyError(f"ux.product in {source} must be a string")
    artifact = artifact.strip()
    product = product.strip()
    if enabled and not artifact:
        raise UXPolicyError(f"ux.artifact in {source} is required when UX support is enabled")
    if enabled and not product:
        raise UXPolicyError(f"ux.product in {source} is required when UX support is enabled")
    if len(artifact) > 2048:
        raise UXPolicyError(f"ux.artifact in {source} is too long")
    if len(product) > 128:
        raise UXPolicyError(f"ux.product in {source} is too long")
    return UXRepositoryPolicy(enabled=enabled, artifact=artifact, product=product)


def load_policy(repo: Path) -> UXRepositoryPolicy:
    repo = repo.expanduser().resolve()
    path = repo / REPO_CONFIG
    if not path.is_file():
        return UXRepositoryPolicy()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise UXPolicyError(f"invalid AutoDev repository config: {path}") from exc
    if not isinstance(raw, dict):
        raise UXPolicyError(f"AutoDev repository config must be a JSON object: {path}")
    return parse_ux_policy(raw.get("ux"), source=str(path))


def update_locked_reference(
    repo: Path,
    *,
    expected_reference: str,
    immutable_reference: str,
) -> None:
    repo = repo.expanduser().resolve()
    path = repo / REPO_CONFIG
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise UXPolicyError(f"invalid AutoDev repository config: {path}") from exc
    if not isinstance(raw, dict):
        raise UXPolicyError(f"AutoDev repository config must be a JSON object: {path}")
    ux = raw.get("ux")
    policy = parse_ux_policy(ux, source=str(path))
    if not isinstance(ux, dict):
        raise UXPolicyError(f"ux in {path} must be a JSON object")
    if policy.artifact != expected_reference:
        raise UXPolicyError(
            "UX repository policy changed during locking; refusing to overwrite the newer reference"
        )
    locked = immutable_reference.strip()
    if not locked:
        raise UXPolicyError("resolver did not return an immutable reference to lock")
    updated_ux = dict(ux)
    updated_ux["artifact"] = locked
    raw["ux"] = updated_ux
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(raw, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temp.replace(path)
