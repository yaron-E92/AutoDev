from __future__ import annotations

import json
import os
import re
import shutil
import sys
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, TextIO
from automation import run_real_issue_core as _core
from automation.run_real_issue_core import *  # noqa: F401,F403
from automation.model_providers import ModelConfig, ModelProvider, ProviderResponse, create_provider, load_provider_config
from automation.model_roles import (
    MODEL_ROLES,
    ModelInvocationError,
    append_invocation_metadata,
    invoke_model,
    model_config_to_dict,
    resolve_role_configs,
    safe_role_metadata,
)
from automation.prompt_policies import (
    compose_prompt,
    resolve_prompt_policies,
    role_policy_metadata,
    safe_prompt_policy_metadata,
)
from automation.run_manifest import (
    MANIFEST_NAME,
    ManifestError,
    build_role_snapshot,
    complete_stage,
    create_manifest,
    hash_file,
    hash_text,
    load_manifest,
    manifest_path,
    next_stage,
    reconcile_role_snapshots,
    record_failure,
    record_stage_state,
    render_status,
    save_manifest,
    stage_completed,
    stage_role_fingerprint,
    sync_invocations,
    update_pr,
    validate_artifacts,
)
from automation.semantic_verifier import (
    SemanticSettings,
    SemanticVerifierError,
    build_schema_repair_prompt,
    build_semantic_prompt,
    build_semantic_repair_prompt,
    collect_changed_files,
    collect_current_diff,
    parse_semantic_output,
    resolve_semantic_settings,
    safe_semantic_metadata,
    write_final_verdict,
    write_semantic_result,
)
from automation.issue_run_repository import (
    _is_expected_autodev_commit,
)
from automation.issue_run_session import (
    _ACTIVE_MANIFEST,
    _CORE_CREATE_DRAFT_PR,
    _active_manifest_path,
    _policies_or_default,
    _roles_or_legacy,
    _stage_output_hash,
)

def create_draft_pr(repo, github_repo, issue, issue_text, out_dir, reader_config, coder_config, stream):
    if _ACTIVE_MANIFEST.get() is None:
        return _CORE_CREATE_DRAFT_PR(repo, github_repo, issue, issue_text, out_dir, reader_config, coder_config, stream)

    manifest_file = _active_manifest_path()
    manifest = load_manifest(manifest_file)
    if stage_completed(manifest, "pr-created"):
        pr = manifest.get("pr", {})
        url = str(pr.get("url", "")) if isinstance(pr, dict) else ""
        return "Existing PR from run manifest:\n\n" + url + "\n"

    current_branch = run_command(["git", "branch", "--show-current"], cwd=repo, stream=stream).stdout.strip()  # noqa: F405
    existing = _find_existing_pr(repo, github_repo, current_branch, stream)
    changed_paths = changed_worktree_paths(repo, stream)  # noqa: F405
    if existing is not None:
        if changed_paths:
            raise RunnerError("existing PR detected but the working tree has additional uncommitted changes", 2)  # noqa: F405
        _record_pr_checkpoint(out_dir, repo, existing, stream)
        return f"Existing PR detected:\n\n{existing['url']}\n"

    if current_branch in {"main", "master"}:
        raise RunnerError("Refusing to create a PR from the main branch.", 2)  # noqa: F405
    run_artifacts = [path for path in changed_paths if is_relative_to(repo / path, out_dir)]  # noqa: F405
    if run_artifacts:
        raise RunnerError("Refusing pr mode because --out files would be committed: " + ", ".join(run_artifacts), 2)  # noqa: F405

    target = manifest.get("target", {})
    base_sha = str(target.get("base_sha", "")) if isinstance(target, dict) else ""
    if changed_paths:
        run_command(["git", "add", "--", *changed_paths], cwd=repo, stream=stream)  # noqa: F405
        run_command(["git", "commit", "-m", f"Implement issue {issue} with AutoDev runner"], cwd=repo, stream=stream)  # noqa: F405
    else:
        head = run_command(["git", "rev-parse", "HEAD"], cwd=repo, stream=stream).stdout.strip()  # noqa: F405
        if head == base_sha or not _is_expected_autodev_commit(repo, stream, issue):
            raise RunnerError("No verified AutoDev changes are available to create or resume the PR.", 2)  # noqa: F405

    run_command(["git", "push", "-u", "origin", current_branch], cwd=repo, stream=stream)  # noqa: F405
    body_path = out_dir / "draft-pr-body.md"
    write_text(body_path, build_pr_body(issue, issue_text, out_dir, reader_config, coder_config))  # noqa: F405
    title = first_issue_title(issue_text) or f"Issue #{issue}"  # noqa: F405
    result = run_command(
        [
            "gh", "pr", "create",
            "--repo", github_repo,
            "--draft",
            "--title", title,
            "--body-file", str(body_path),
            "--base", "main",
            "--head", current_branch,
        ],
        cwd=repo,
        stream=stream,
    )  # noqa: F405
    existing = _find_existing_pr(repo, github_repo, current_branch, stream)
    if existing is None:
        url = result.stdout.strip()
        existing = {"number": None, "url": url, "state": "open"}
    _record_pr_checkpoint(out_dir, repo, existing, stream)
    return "Draft PR created:\n\n" + str(existing["url"]) + "\n"

def _find_existing_pr(repo: Path, github_repo: str, branch: str, stream: TextIO) -> dict[str, object] | None:
    result = run_command(
        [
            "gh", "pr", "list",
            "--repo", github_repo,
            "--head", branch,
            "--state", "all",
            "--limit", "5",
            "--json", "number,url,state,isDraft",
        ],
        cwd=repo,
        stream=stream,
        check=False,
    )  # noqa: F405
    if result.returncode != 0:
        raise RunnerError("Unable to determine whether a PR already exists; refusing duplicate-PR risk.", 2)  # noqa: F405
    try:
        values = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RunnerError("gh pr list returned invalid JSON; refusing duplicate-PR risk.", 2) from exc  # noqa: F405
    if not isinstance(values, list):
        raise RunnerError("gh pr list returned an unexpected response; refusing duplicate-PR risk.", 2)  # noqa: F405
    if not values:
        return None
    first = values[0]
    if not isinstance(first, dict):
        raise RunnerError("gh pr list returned an invalid PR record; refusing duplicate-PR risk.", 2)  # noqa: F405
    return first

def _record_pr_checkpoint(out_dir: Path, repo: Path, pr: dict[str, object], stream: TextIO) -> None:
    head_sha = run_command(["git", "rev-parse", "HEAD"], cwd=repo, stream=stream).stdout.strip()  # noqa: F405
    update_pr(
        _active_manifest_path(),
        number=int(pr["number"]) if isinstance(pr.get("number"), int) else None,
        url=str(pr.get("url", "")),
        state=str(pr.get("state", "")),
    )
    complete_stage(
        _active_manifest_path(),
        "pr-created",
        run_root=out_dir,
        inputs={"semantic_output": _stage_output_hash(load_manifest(_active_manifest_path()), "semantic-verified")},
        details={"head_sha": head_sha, "number": pr.get("number"), "url": pr.get("url", "")},
    )

def build_pr_body(issue, issue_text, out_dir, reader_config, coder_config):
    roles = _roles_or_legacy(reader_config, coder_config)
    semantic_verdict = read_optional_text(out_dir / "verification" / "final-verdict.json").strip()  # noqa: F405
    manifest_path_value = _ACTIVE_MANIFEST.get()
    manifest = load_manifest(manifest_path_value) if manifest_path_value is not None and manifest_path_value.is_file() else {}
    return "\n".join(
        [
            f"Closes #{issue}", "", "Generated by AutoDev.", "", "## Summary", "",
            read_optional_text(out_dir / "coder-plan.md").strip() or "See implementation diff.",  # noqa: F405
            "", "## Deterministic Verification", "",
            read_optional_text(out_dir / "verification-result-summary.md").strip(),  # noqa: F405
            "", "## Semantic Verification", "",
            "```json", semantic_verdict or json.dumps({"enabled": False}, indent=2), "```",
            "", "## Provider Roles", "", "```json",
            json.dumps(safe_role_metadata(roles), indent=2, sort_keys=True),
            "```", "", "## Prompt Policy", "", "```json",
            json.dumps(safe_prompt_policy_metadata(_policies_or_default()), indent=2, sort_keys=True),
            "```", "", "## Run Manifest", "",
            f"Run ID: {manifest.get('run_id', '')}",
            "",
        ]
    )
