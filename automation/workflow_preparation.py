from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from automation import repository_identity
from automation.semantic_contract import SemanticVerifierError
from automation.semantic_invocation import prepare_semantic_repair_prompt
from automation.semantic_prompts import extract_acceptance_criteria
from automation.semantic_schema import parse_semantic_output
from automation.semantic_text import render_template
from automation.workflow_commands import (
    gh,
    gh_json,
)
from automation.workflow_contract import (
    AUTODEV_ROOT,
    CURRENT_DIR,
    VERIFICATION_PROOF_VERSION,
    WorkflowStageError,
    issue_number_from_arguments,
    safe_slug,
)
from automation.workflow_diagnostics import (
    _record_shipment_diagnostics,
)
from automation.workflow_prompts import (
    resolve_profiles,
)
from automation.workflow_storage import (
    _file_sha256,
    _json_evidence,
    read_json,
    write_json,
    write_text,
)
from automation.workflow_workspace import (
    validate_prepared_worktree,
    write_workspace_snapshot,
)

def ensure_prepared_issue(
    repo: Path,
    arguments: str,
    *,
    autodev_root: Path = AUTODEV_ROOT,
    runner: Callable[..., object] = subprocess.run,
) -> Path:
    current = repo / CURRENT_DIR
    requested_issue = issue_number_from_arguments(arguments)
    existing = read_json(current / "state.json")
    current_issue = int(existing.get("IssueNumber", 0) or 0) if isinstance(existing, dict) else 0
    if current.is_dir() and requested_issue and current_issue == requested_issue:
        return current
    if requested_issue == 0:
        raise WorkflowStageError("no prepared AutoDev issue is available; pass an issue number")

    try:
        repo_full = repository_identity.resolve_github_repository(repo, runner=runner)
    except repository_identity.RepositoryIdentityError as exc:
        raise WorkflowStageError(str(exc)) from exc
    owner, repo_name = repository_identity.split_github_repository(repo_full)

    issue = gh_json(
        repo,
        ["issue", "view", str(requested_issue), "--repo", repo_full, "--json", "number,title,body,url,labels"],
        runner=runner,
    )
    labels = [
        str(item.get("name", ""))
        for item in issue.get("labels", [])
        if isinstance(item, dict) and str(item.get("name", "")).strip()
    ]

    base = os.environ.get("BASE_BRANCH", "main").strip() or "main"
    remote = os.environ.get("REMOTE_NAME", "origin").strip() or "origin"
    base_ref = gh_json(repo, ["api", f"repos/{repo_full}/git/ref/heads/{base}"], runner=runner)
    base_object = base_ref.get("object", {})
    base_sha = str(base_object.get("sha", "")) if isinstance(base_object, dict) else ""
    if not base_sha:
        raise WorkflowStageError(
            f"could not resolve prepared base branch {base}; GitHub response: {_json_evidence(base_ref)}"
        )
    base_commit = gh_json(repo, ["api", f"repos/{repo_full}/git/commits/{base_sha}"], runner=runner)
    tree = base_commit.get("tree", {})
    base_tree_sha = str(tree.get("sha", "")) if isinstance(tree, dict) else ""
    if not base_tree_sha:
        raise WorkflowStageError(
            f"prepared base commit {base_sha} did not contain tree.sha; GitHub response: {_json_evidence(base_commit)}"
        )

    prepared_local_head = validate_prepared_worktree(repo, base_sha, runner=runner)

    profiles_path = Path(os.environ.get("PROFILES_PATH", str(autodev_root / "codex-profiles.json"))).expanduser()
    profiles_csv, local_check, stack_context = resolve_profiles(
        labels,
        profiles_path,
        explicit_profiles=os.environ.get("PROFILES", ""),
        explicit_local_check=os.environ.get("LOCAL_CHECK", ""),
        explicit_stack_context=os.environ.get("STACK_CONTEXT", ""),
        autodev_root=autodev_root,
    )

    gh(
        repo,
        ["issue", "edit", str(requested_issue), "--repo", repo_full, "--add-label", "autodev:running"],
        runner=runner,
    )

    current.parent.mkdir(parents=True, exist_ok=True)
    if current.exists():
        shutil.rmtree(current)
    current.mkdir(parents=True)

    title = str(issue.get("title", "")).strip()
    url = str(issue.get("url", "")).strip()
    body = str(issue.get("body", "") or "")
    issue_text = f"# GitHub Issue #{requested_issue}: {title}\n\nURL: {url}\n\n{body}\n"
    write_text(current / "issue.md", issue_text)
    snapshot_path = current / "workspace-snapshot.json"
    write_workspace_snapshot(repo, snapshot_path)
    prepared_snapshot_hash = _file_sha256(snapshot_path)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    branch_name = f"autodev/{safe_slug(f'issue-{requested_issue}-{title}')}-{timestamp}"
    state = {
        "Status": "Prepared",
        "ApiCommitMode": True,
        "VerificationProofVersion": VERIFICATION_PROOF_VERSION,
        "CreatedAt": datetime.now(timezone.utc).isoformat(),
        "Timestamp": timestamp,
        "Username": owner,
        "Repo": repo_name,
        "RepoFullName": repo_full,
        "IssueNumber": requested_issue,
        "IssueTitle": title,
        "IssueUrl": url,
        "IssueText": issue_text,
        "Labels": labels,
        "Base": base,
        "Remote": remote,
        "BranchName": branch_name,
        "BaseSha": base_sha,
        "BaseTreeSha": base_tree_sha,
        "PreparedLocalHeadSha": prepared_local_head,
        "PreparedSnapshotHash": prepared_snapshot_hash,
        "LastCommitSha": "",
        "ProfilesCsv": profiles_csv,
        "LocalCheck": local_check,
        "StackContext": stack_context,
        "PromptDir": os.environ.get("PROMPT_DIR", str(autodev_root / "promptTemplates")),
        "ProfilesPath": str(profiles_path),
        "ProviderProfile": os.environ.get("PROVIDER_PROFILE", ""),
        "RunDir": str(current.resolve()),
        "PrUrl": "",
        "PrNumber": 0,
        "PrHeadSha": "",
        "LastLocalCheckPassed": False,
        "Auth": {
            "GitHubTokenSecretName": os.environ.get("GITHUB_TOKEN_SECRET_NAME", ""),
            "KeePassCliPath": os.environ.get("KEEPASS_CLI", ""),
            "KeePassDatabasePath": os.environ.get("KEEPASS_DB", ""),
            "KeePassEntryPath": os.environ.get("KEEPASS_ENTRY_PATH", ""),
            "KeePassKeyFilePath": os.environ.get("KEEPASS_KEY_FILE", ""),
            "KeePassNoPassword": False,
            "GhConfigDir": os.environ.get("GH_CONFIG_DIR", ""),
        },
    }
    write_json(current / "state.json", state)
    _record_shipment_diagnostics(
        current,
        prepared_base_sha=base_sha,
        prepared_base_tree=base_tree_sha,
        prepared_local_head=prepared_local_head,
        prepared_snapshot_hash=prepared_snapshot_hash,
    )
    return current
