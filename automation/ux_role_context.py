from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

from automation import ux_resolver, ux_workflow


class UXRoleContextError(RuntimeError):
    """A deterministic failure while building UX role context."""


def prepare_role_context(
    repo: Path,
    current: Path,
    role: str,
    issue_text: str,
) -> tuple[str, dict[str, object]]:
    state = _read_json(current / "state.json")
    expected = state.get("UXArtifact", {}) if isinstance(state, dict) else {}
    if not isinstance(expected, dict) or not expected:
        return "", {}

    try:
        artifact = ux_workflow.resolve_configured(
            repo,
            unattended=bool(os.environ.get("AUTODEV_HEADLESS", "").strip()),
        )
    except (ux_resolver.UXResolutionError, ValueError) as exc:
        raise UXRoleContextError(f"UX role context resolution failed: {exc}") from exc
    if artifact is None:
        raise UXRoleContextError(
            "prepared run records a UX artifact but repository UX policy is now disabled"
        )

    expected_identity = str(expected.get("immutable_identity", "") or "")
    if expected_identity and artifact.immutable_identity != expected_identity:
        raise UXRoleContextError(
            "resolved UX artifact identity changed after run preparation; start a new run explicitly"
        )

    manifest = artifact.manifest
    screen_ids = _mentioned_ids(issue_text, tuple((manifest.screens or {}).keys()))
    state_ids = _mentioned_ids(issue_text, tuple((manifest.states or {}).keys()))
    journey_ids = _mentioned_ids(issue_text, tuple((manifest.journey_files or {}).keys()))
    selected = manifest.selected_paths(
        screen_ids=screen_ids,
        state_ids=state_ids,
        journey_ids=journey_ids,
        include_journeys=False,
    )

    text_sections: list[str] = []
    referenced_paths: list[str] = []
    file_hashes: dict[str, str] = {}
    total_text = 0
    root = artifact.local_root.resolve()
    for relative in selected:
        path = (root / relative).resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise UXRoleContextError(f"selected UX path escapes artifact root: {relative}") from exc
        if not path.is_file():
            raise UXRoleContextError(f"selected UX input is missing: {relative}")
        data = path.read_bytes()
        file_hashes[relative] = hashlib.sha256(data).hexdigest()
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            referenced_paths.append(relative)
            continue
        if total_text >= 96_000:
            referenced_paths.append(relative)
            continue
        remaining = 96_000 - total_text
        bounded = text[:remaining]
        total_text += len(bounded)
        text_sections.append(f"### {relative}\n\n{bounded.rstrip()}\n")
        if len(text) > len(bounded):
            referenced_paths.append(relative)

    fingerprint_payload = {
        "immutable_identity": artifact.immutable_identity,
        "issue_sha256": hashlib.sha256(issue_text.encode("utf-8", errors="replace")).hexdigest(),
        "screen_ids": list(screen_ids),
        "state_ids": list(state_ids),
        "journey_ids": list(journey_ids),
        "selected_paths": list(selected),
        "file_sha256": file_hashes,
    }
    fingerprint = hashlib.sha256(
        json.dumps(fingerprint_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    evidence: dict[str, object] = {
        "role": role,
        "ux_artifact": {
            "immutable_identity": artifact.immutable_identity,
            "product": artifact.manifest.product,
            "bundle_schema": artifact.manifest.schema,
        },
        "ux_context": {
            "contract": artifact.manifest.contract,
            "principles": artifact.manifest.principles,
            "annexes": list(artifact.manifest.annexes),
            "journeys": list(journey_ids),
            "screens": list(screen_ids),
            "states": list(state_ids),
            "selected_paths": list(selected),
            "file_sha256": file_hashes,
            "non_text_or_truncated_references": sorted(referenced_paths),
        },
        "selection_basis_sha256": fingerprint_payload["issue_sha256"],
        "ux_context_fingerprint": fingerprint,
    }
    _write_json(current / f"ux-context-{role}.json", evidence)
    _persist_manifest_evidence(current, role, evidence)

    prompt = (
        "\n\n# Pinned UX authority\n\n"
        f"This run is pinned to UX artifact `{artifact.immutable_identity}` for product "
        f"`{artifact.manifest.product}`. Treat the interaction contract and shared principles below "
        "as product authority for UX-bearing work. Current repository presentation topology is evidence, "
        "not automatically the intended information architecture.\n\n"
    )
    if text_sections:
        prompt += "\n".join(text_sections)
    if referenced_paths:
        prompt += (
            "\n### Non-text or bounded UX references\n\n"
            "These selected bundle-relative paths are part of the effective UX context and are included "
            "in its fingerprint; inspect them when the runtime supports the file type:\n\n"
            + "\n".join(f"- `{item}`" for item in sorted(referenced_paths))
            + "\n"
        )
    prompt += (
        "\nAutoDev records this role's selected UX inputs in "
        f"`.autodev-run/current/ux-context-{role}.json` and the durable run manifest.\n"
    )
    return prompt, evidence


def context_fingerprint(current: Path, role: str) -> str:
    value = _read_json(current / f"ux-context-{role}.json")
    return str(value.get("ux_context_fingerprint", "") or "") if isinstance(value, dict) else ""


def _persist_manifest_evidence(current: Path, role: str, evidence: dict[str, object]) -> None:
    path = current / "run-manifest.json"
    if not path.is_file():
        return
    manifest = _read_json(path)
    if not manifest:
        raise UXRoleContextError("run manifest is unreadable while recording UX role evidence")
    contexts = manifest.setdefault("ux_role_contexts", {})
    if not isinstance(contexts, dict):
        raise UXRoleContextError("run manifest ux_role_contexts is malformed")
    contexts[role] = evidence
    _write_json(path, manifest)


def _mentioned_ids(text: str, candidates: tuple[str, ...]) -> tuple[str, ...]:
    selected: list[str] = []
    for candidate in sorted(candidates):
        if not candidate:
            continue
        pattern = rf"(?<![\w-]){re.escape(candidate)}(?![\w-])"
        if re.search(pattern, text, flags=re.IGNORECASE):
            selected.append(candidate)
    return tuple(selected)


def _read_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
