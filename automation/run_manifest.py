from __future__ import annotations

import hashlib
import json
import re

from automation import external_error_sanitizer
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


SCHEMA_VERSION = 1
MANIFEST_NAME = "run-manifest.json"
PRIMARY_STAGES = (
    "issue-selected",
    "repository-read",
    "handoff-synthesized",
    "plan-created",
    "implementation-generated",
    "patch-applied",
    "deterministic-verified",
    "semantic-verified",
    "pr-created",
)
OPTIONAL_STAGES = ("repair-generated",)
ALL_STAGES = PRIMARY_STAGES + OPTIONAL_STAGES
ROLE_STAGE = {
    "reader": "repository-read",
    "synthesizer": "handoff-synthesized",
    "planner": "plan-created",
    "implementer": "implementation-generated",
    "fixer": "repair-generated",
    "verifier": "semantic-verified",
}


class ManifestError(ValueError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def manifest_path(run_dir: Path) -> Path:
    return run_dir / MANIFEST_NAME


def hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()


def hash_json(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hash_text(payload)


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sanitized_url(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parts = urlsplit(text)
    except ValueError:
        return "configured"
    hostname = parts.hostname or ""
    if parts.port is not None:
        hostname += f":{parts.port}"
    return urlunsplit((parts.scheme, hostname, parts.path, "", ""))


def build_role_snapshot(
    config: dict[str, object],
    safe_metadata: dict[str, object],
    *,
    prompt_policy: dict[str, object] | None = None,
) -> dict[str, object]:
    fingerprint_source = _fingerprint_source(config)
    safe = dict(safe_metadata)
    if safe.get("base_url"):
        safe["base_url"] = sanitized_url(safe["base_url"])
    snapshot: dict[str, object] = {
        "fingerprint": hash_json(fingerprint_source),
        "safe_metadata": safe,
    }
    if prompt_policy:
        snapshot["prompt_policy"] = dict(prompt_policy)
    return snapshot


def _fingerprint_source(value: object, key: str = "") -> object:
    normalized_key = key.casefold().replace("-", "_")
    if normalized_key in {
        "authorization",
        "api_key",
        "api_key_value",
        "token",
        "secret",
        "password",
        "cookie",
        "proxy_authorization",
    }:
        return "<secret>"
    if isinstance(value, dict):
        return {
            str(item_key): _fingerprint_source(item_value, str(item_key))
            for item_key, item_value in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_fingerprint_source(item, key) for item in value]
    return value


def create_manifest(
    path: Path,
    *,
    repo_path: Path,
    github_repo: str,
    issue_number: int,
    mode: str,
    base_sha: str,
    branch: str,
    role_snapshots: dict[str, object],
    prompt_policy: dict[str, object] | None = None,
    semantic_verification: dict[str, object] | None = None,
    ux_artifact: dict[str, object] | None = None,
) -> dict[str, object]:
    now = utc_now()
    manifest: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "run_id": str(uuid.uuid4()),
        "created_at": now,
        "updated_at": now,
        "target": {
            "repo_path": str(repo_path.resolve()),
            "github_repo": github_repo,
            "issue_number": issue_number,
            "mode": mode,
            "base_sha": base_sha,
            "branch": branch,
        },
        "current_stage": "",
        "completed_stages": [],
        "stages": {},
        "roles": role_snapshots,
        "prompt_policy": dict(prompt_policy or {}),
        "semantic_verification": dict(semantic_verification or {}),
        "ux_artifact": dict(ux_artifact or {}),
        "invocations": [],
        "failure": {},
        "pr": {},
        "invalidations": [],
    }
    save_manifest(path, manifest)
    return manifest


def load_manifest(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ManifestError(f"run manifest not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ManifestError(f"run manifest is not valid JSON: {path}") from exc
    if not isinstance(value, dict):
        raise ManifestError("run manifest must be a JSON object")
    validate_manifest(value)
    return value


def validate_manifest(value: dict[str, object]) -> None:
    version = value.get("schema_version")
    if version != SCHEMA_VERSION:
        raise ManifestError(
            f"unsupported run manifest schema version: {version}; expected {SCHEMA_VERSION}"
        )
    target = value.get("target")
    if not isinstance(target, dict):
        raise ManifestError("run manifest target must be an object")
    for key in ("repo_path", "github_repo", "issue_number", "mode", "base_sha", "branch"):
        if target.get(key) in (None, ""):
            raise ManifestError(f"run manifest target is missing {key}")
    completed = value.get("completed_stages")
    if not isinstance(completed, list) or any(stage not in ALL_STAGES for stage in completed):
        raise ManifestError("run manifest completed_stages is invalid")
    stages = value.get("stages")
    if not isinstance(stages, dict):
        raise ManifestError("run manifest stages must be an object")
    unknown = sorted(set(stages) - set(ALL_STAGES))
    if unknown:
        raise ManifestError("run manifest contains unknown stage(s): " + ", ".join(unknown))
    roles = value.get("roles")
    if not isinstance(roles, dict):
        raise ManifestError("run manifest roles must be an object")
    ux_artifact = value.get("ux_artifact", {})
    if not isinstance(ux_artifact, dict):
        raise ManifestError("run manifest ux_artifact must be an object")


def save_manifest(path: Path, manifest: dict[str, object]) -> None:
    manifest["updated_at"] = utc_now()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def stage_completed(manifest: dict[str, object], stage: str) -> bool:
    return stage in manifest.get("completed_stages", [])


def complete_stage(
    path: Path,
    stage: str,
    *,
    run_root: Path,
    artifacts: list[Path | str] | tuple[Path | str, ...] = (),
    inputs: object | None = None,
    details: dict[str, object] | None = None,
) -> dict[str, object]:
    if stage not in ALL_STAGES:
        raise ManifestError(f"unknown run stage: {stage}")
    manifest = load_manifest(path)
    artifact_hashes: dict[str, str] = {}
    for artifact in artifacts:
        artifact_path = Path(artifact)
        absolute = artifact_path if artifact_path.is_absolute() else run_root / artifact_path
        if not absolute.is_file():
            raise ManifestError(f"cannot complete {stage}; artifact is missing: {absolute}")
        try:
            relative = absolute.resolve().relative_to(run_root.resolve()).as_posix()
        except ValueError as exc:
            raise ManifestError(f"stage artifact is outside run directory: {absolute}") from exc
        artifact_hashes[relative] = hash_file(absolute)

    stages = manifest.setdefault("stages", {})
    assert isinstance(stages, dict)
    previous = stages.get(stage)
    history: list[object] = []
    if isinstance(previous, dict):
        old_history = previous.get("history")
        if isinstance(old_history, list):
            history.extend(old_history)
        history.append({key: value for key, value in previous.items() if key != "history"})
    record: dict[str, object] = {
        "status": "completed",
        "completed_at": utc_now(),
        "input_hash": hash_json(inputs if inputs is not None else {}),
        "artifacts": artifact_hashes,
        "output_hash": hash_json(artifact_hashes),
        "details": dict(details or {}),
    }
    if history:
        record["history"] = history
    stages[stage] = record

    completed = manifest.setdefault("completed_stages", [])
    assert isinstance(completed, list)
    if stage not in completed:
        completed.append(stage)
    completed.sort(key=_stage_sort_key)
    manifest["current_stage"] = stage
    manifest["failure"] = {}
    save_manifest(path, manifest)
    return manifest


def record_stage_state(
    path: Path,
    stage: str,
    *,
    status: str,
    details: dict[str, object] | None = None,
) -> dict[str, object]:
    if stage not in ALL_STAGES:
        raise ManifestError(f"unknown run stage: {stage}")
    manifest = load_manifest(path)
    stages = manifest.setdefault("stages", {})
    assert isinstance(stages, dict)
    existing = stages.get(stage)
    record = dict(existing) if isinstance(existing, dict) else {}
    record.update({"status": status, "updated_at": utc_now(), "details": dict(details or {})})
    stages[stage] = record
    manifest["current_stage"] = stage
    save_manifest(path, manifest)
    return manifest


def record_failure(
    path: Path,
    *,
    classification: str,
    reason: str,
    stage: str = "",
) -> dict[str, object]:
    manifest = load_manifest(path)
    manifest["failure"] = {
        "classification": classification,
        "reason": _safe_reason(reason),
        "stage": stage or str(manifest.get("current_stage") or ""),
        "recorded_at": utc_now(),
    }
    save_manifest(path, manifest)
    return manifest


def sync_invocations(path: Path, invocations_path: Path) -> dict[str, object]:
    manifest = load_manifest(path)
    try:
        raw = json.loads(invocations_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raw = []
    records = raw if isinstance(raw, list) else []
    manifest["invocations"] = [
        sanitized_invocation(record)
        for record in records
        if isinstance(record, dict)
    ]
    save_manifest(path, manifest)
    return manifest


def sanitized_invocation(record: dict[str, object]) -> dict[str, object]:
    allowed = {
        "role",
        "attempt",
        "retry_count",
        "provider",
        "transport",
        "model",
        "profile_name",
        "status",
        "failure_classification",
        "status_code",
        "retry_after",
        "usage",
        "reported_cost",
        "reported_model",
        "started_at",
        "ended_at",
        "elapsed_seconds",
        "prompt_policy_mode",
        "prompt_policy_version",
        "compression",
    }
    safe = {key: value for key, value in record.items() if key in allowed}
    if record.get("base_url"):
        safe["endpoint"] = sanitized_url(record["base_url"])
    compression = safe.get("compression")
    if isinstance(compression, dict):
        safe["compression"] = {
            key: value
            for key, value in compression.items()
            if key not in {"prompt", "response", "authorization", "api_key", "token"}
        }
    return safe


def mark_stage_artifacts_refreshable(
    path: Path,
    stage: str,
    artifacts: list[str] | tuple[str, ...],
) -> dict[str, object]:
    """Refresh deterministic sidecar hashes without changing semantic stage identity."""
    manifest = load_manifest(path)
    stages = manifest.get("stages", {})
    if not isinstance(stages, dict):
        raise ManifestError("run manifest stages must be an object")
    record = stages.get(stage)
    if not isinstance(record, dict):
        return manifest

    artifact_hashes = record.get("artifacts", {})
    if not isinstance(artifact_hashes, dict):
        raise ManifestError(f"{stage}: artifact map is invalid")

    details = record.get("details", {})
    details = dict(details) if isinstance(details, dict) else {}
    existing = details.get("refreshable_artifacts", [])
    values = {
        str(value)
        for value in existing
        if isinstance(value, str) and value
    } if isinstance(existing, list) else set()
    refreshed_hashes = details.get("deterministic_refreshed_artifact_hashes", {})
    refreshed_hashes = dict(refreshed_hashes) if isinstance(refreshed_hashes, dict) else {}

    run_root = path.parent
    refreshed: list[str] = []
    for artifact in artifacts:
        relative = str(artifact or "")
        if not relative:
            continue
        values.add(relative)
        target = run_root / relative
        if target.is_file() and relative in artifact_hashes:
            refreshed_hashes[relative] = hash_file(target)
            refreshed.append(relative)

    details["refreshable_artifacts"] = sorted(values)
    if refreshed:
        details["deterministic_refreshed_artifact_hashes"] = refreshed_hashes
        details["deterministic_refresh_count"] = int(
            details.get("deterministic_refresh_count", 0) or 0
        ) + 1
        details["deterministic_refreshed_at"] = utc_now()
        details["deterministic_refreshed_artifacts"] = sorted(refreshed)
    record["details"] = details
    stages[stage] = record
    # Keep the original artifact map/output_hash immutable: downstream semantic
    # stages are bound to the accepted Reader checkpoint. Refreshed deterministic
    # sidecars have their current hashes recorded separately and are still
    # fail-closed by validate_artifacts().
    save_manifest(path, manifest)
    return manifest


def validate_artifacts(manifest: dict[str, object], run_root: Path) -> list[str]:
    problems: list[str] = []
    stages = manifest.get("stages", {})
    if not isinstance(stages, dict):
        return ["manifest stages are invalid"]
    for stage in manifest.get("completed_stages", []):
        record = stages.get(stage)
        if not isinstance(record, dict):
            problems.append(f"{stage}: stage record is missing")
            continue
        artifacts = record.get("artifacts", {})
        if not isinstance(artifacts, dict):
            problems.append(f"{stage}: artifact map is invalid")
            continue
        details = record.get("details", {})
        refreshed_hashes = (
            details.get("deterministic_refreshed_artifact_hashes", {})
            if isinstance(details, dict)
            else {}
        )
        if not isinstance(refreshed_hashes, dict):
            refreshed_hashes = {}
        for relative, expected in artifacts.items():
            artifact = run_root / str(relative)
            if not artifact.is_file():
                problems.append(f"{stage}: missing artifact {relative}")
                continue
            current_expected = str(refreshed_hashes.get(str(relative), expected))
            if hash_file(artifact) != current_expected:
                problems.append(f"{stage}: artifact drift detected for {relative}")
    return problems


def invalidation_start_for_role(role: str) -> str:
    try:
        return ROLE_STAGE[role]
    except KeyError as exc:
        raise ManifestError(f"unknown model role for invalidation: {role}") from exc


def invalidated_stages_for_role(manifest: dict[str, object], role: str) -> list[str]:
    start = invalidation_start_for_role(role)
    completed = set(str(stage) for stage in manifest.get("completed_stages", []))
    if start == "repair-generated":
        candidates = ("repair-generated", "deterministic-verified", "semantic-verified", "pr-created")
    else:
        index = PRIMARY_STAGES.index(start)
        candidates = PRIMARY_STAGES[index:] + (("repair-generated",) if index <= PRIMARY_STAGES.index("patch-applied") else ())
    return [stage for stage in candidates if stage in completed]


def invalidate_role(path: Path, role: str, *, reason: str = "configuration changed") -> list[str]:
    manifest = load_manifest(path)
    stages_to_clear = invalidated_stages_for_role(manifest, role)
    if not stages_to_clear:
        return []
    stages = manifest.get("stages", {})
    completed = manifest.get("completed_stages", [])
    assert isinstance(stages, dict) and isinstance(completed, list)
    invalidated = manifest.setdefault("invalidations", [])
    assert isinstance(invalidated, list)
    for stage in stages_to_clear:
        record = stages.pop(stage, None)
        if stage in completed:
            completed.remove(stage)
        invalidated.append(
            {
                "stage": stage,
                "role": role,
                "reason": reason,
                "invalidated_at": utc_now(),
                "previous_output_hash": record.get("output_hash", "") if isinstance(record, dict) else "",
            }
        )
    manifest["current_stage"] = completed[-1] if completed else ""
    manifest["failure"] = {}
    save_manifest(path, manifest)
    return stages_to_clear


def reconcile_role_snapshots(
    path: Path,
    current: dict[str, object],
    *,
    explicit_invalidations: set[str] | None = None,
) -> dict[str, list[str]]:
    explicit_invalidations = explicit_invalidations or set()
    for role in explicit_invalidations:
        invalidate_role(path, role, reason="explicit role invalidation")

    manifest = load_manifest(path)
    existing = manifest.get("roles", {})
    assert isinstance(existing, dict)
    blocked: dict[str, list[str]] = {}
    for role, snapshot in current.items():
        previous = existing.get(role)
        previous_fingerprint = previous.get("fingerprint") if isinstance(previous, dict) else None
        current_fingerprint = snapshot.get("fingerprint") if isinstance(snapshot, dict) else None
        if previous_fingerprint and current_fingerprint != previous_fingerprint:
            affected = invalidated_stages_for_role(manifest, role)
            if affected and role not in explicit_invalidations:
                blocked[role] = affected
    if blocked:
        detail = "; ".join(
            f"{role} -> {', '.join(stages)}"
            for role, stages in sorted(blocked.items())
        )
        raise ManifestError(
            "execution-affecting role configuration changed for completed work; "
            f"resume requires --invalidate-role for: {detail}"
        )

    manifest = load_manifest(path)
    manifest["roles"] = current
    save_manifest(path, manifest)
    return {
        role: invalidated_stages_for_role(manifest, role)
        for role in explicit_invalidations
    }


def next_stage(manifest: dict[str, object]) -> str:
    target = manifest.get("target", {})
    mode = str(target.get("mode", "plan-only")) if isinstance(target, dict) else "plan-only"
    if mode == "plan-only":
        applicable = PRIMARY_STAGES[: PRIMARY_STAGES.index("plan-created") + 1]
    elif mode == "implement":
        applicable = PRIMARY_STAGES[: PRIMARY_STAGES.index("semantic-verified") + 1]
    else:
        applicable = PRIMARY_STAGES
    completed = set(str(stage) for stage in manifest.get("completed_stages", []))
    return next((stage for stage in applicable if stage not in completed), "complete")


def render_status(
    manifest: dict[str, object],
    *,
    requested_invalidations: list[str] | None = None,
    artifact_problems: list[str] | None = None,
) -> str:
    completed = [str(stage) for stage in manifest.get("completed_stages", [])]
    roles = manifest.get("roles", {})
    invocations = manifest.get("invocations", [])
    failure = manifest.get("failure", {})
    artifact_problems = artifact_problems or []
    lines = [
        f"Run ID: {manifest.get('run_id', '')}",
        "Completed stages: " + (", ".join(completed) if completed else "(none)"),
        f"Next stage: {next_stage(manifest)}",
        f"Safely resumable: {'no' if artifact_problems else 'yes'}",
        "Roles:",
    ]
    if isinstance(roles, dict):
        for role, snapshot in roles.items():
            safe = snapshot.get("safe_metadata", {}) if isinstance(snapshot, dict) else {}
            if not isinstance(safe, dict):
                safe = {}
            lines.append(
                "  "
                + f"{role}: {safe.get('transport', safe.get('provider', ''))} "
                + f"{safe.get('profile_name', '')} {safe.get('model', '')}".strip()
            )
    if isinstance(invocations, list):
        lines.append(f"Recorded provider attempts: {len(invocations)}")
        failures = [item for item in invocations if isinstance(item, dict) and item.get("status") == "failure"]
        if failures:
            last = failures[-1]
            lines.append(
                "Last provider failure: "
                + str(last.get("failure_classification", "provider_error"))
            )
    if isinstance(failure, dict) and failure:
        lines.append(
            "Last run failure: "
            + str(failure.get("classification", "failure"))
            + (f" ({failure.get('reason')})" if failure.get("reason") else "")
        )
    if artifact_problems:
        lines.append("Artifact drift:")
        lines.extend(f"  - {problem}" for problem in artifact_problems)
    if requested_invalidations:
        lines.append("Requested invalidation:")
        for role in requested_invalidations:
            affected = invalidated_stages_for_role(manifest, role)
            lines.append(f"  {role}: " + (", ".join(affected) if affected else "no completed stages"))
    return "\n".join(lines) + "\n"


def update_pr(path: Path, *, number: int | None, url: str, state: str = "") -> None:
    manifest = load_manifest(path)
    manifest["pr"] = {
        "number": number,
        "url": url,
        "state": state,
        "recorded_at": utc_now(),
    }
    save_manifest(path, manifest)


def stage_role_fingerprint(manifest: dict[str, object], role: str) -> str:
    roles = manifest.get("roles", {})
    if not isinstance(roles, dict):
        return ""
    snapshot = roles.get(role)
    return str(snapshot.get("fingerprint", "")) if isinstance(snapshot, dict) else ""


def area_resume_stage(manifest: dict[str, object]) -> str:
    if stage_completed(manifest, "plan-created"):
        return "plan-created"
    if stage_completed(manifest, "handoff-synthesized"):
        return "handoff-synthesized"
    if stage_completed(manifest, "repository-read"):
        return "repository-read"
    return ""


def _stage_sort_key(stage: str) -> tuple[int, str]:
    if stage in PRIMARY_STAGES:
        return (PRIMARY_STAGES.index(stage), stage)
    if stage == "repair-generated":
        return (PRIMARY_STAGES.index("patch-applied"), stage)
    return (999, stage)


def _safe_reason(value: str) -> str:
    return external_error_sanitizer.sanitize_external_text(
        value,
        max_chars=1000,
        max_lines=8,
    )
