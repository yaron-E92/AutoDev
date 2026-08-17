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

from automation.semantic_verifier import (
    SemanticVerifierError,
    extract_acceptance_criteria,
    parse_semantic_output,
    prepare_semantic_repair_prompt,
    render_template,
)


AUTODEV_ROOT = Path(__file__).resolve().parents[1]
CURRENT_DIR = Path(".autodev-run") / "current"
DIAGNOSTICS_FILE = "run-diagnostics.json"
VERIFICATION_PROOF_VERSION = 1
DEFAULT_CI_CHECK_POLL_ATTEMPTS = 12
DEFAULT_CI_CHECK_POLL_SECONDS = 5.0
STAGES = (
    "preflight",
    "prepare",
    "render-implementer",
    "local-check",
    "semantic",
    "pr-and-ci",
    "ready",
    "blocked",
    "failed",
    "status",
)
DEFAULT_MAX_REPAIR_ATTEMPTS = 3
DEFAULT_MAX_SEMANTIC_REPAIR_ATTEMPTS = 1
FAILURE_CODE_REPAIRABLE = "code-repairable"
FAILURE_TRANSIENT = "transient/retryable-infrastructure"
FAILURE_DETERMINISTIC = "non-retryable-deterministic"
IGNORED_PREFIXES = (
    ".git/",
    ".autodev-run/",
    ".opencode/",
    "bin/",
    "obj/",
    "node_modules/",
    "dist/",
    "build/",
    "coverage/",
    ".vs/",
    ".idea/",
    ".vscode/",
    ".venv/",
    "venv/",
    "__pycache__/",
)


class WorkflowStageError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        classification: str = FAILURE_DETERMINISTIC,
    ) -> None:
        super().__init__(message)
        self.classification = classification


def issue_number_from_arguments(arguments: str) -> int:
    match = re.search(r"(?<!\d)#?(\d+)(?!\d)", arguments or "")
    return int(match.group(1)) if match else 0


def execute_stage(
    name: str,
    repo: Path,
    *,
    arguments: str = "",
    autodev_root: Path = AUTODEV_ROOT,
    attempt: int = 0,
    reason: str = "",
    runner: Callable[..., object] = subprocess.run,
    which: Callable[[str], str | None] = shutil.which,
) -> tuple[int, dict[str, object]]:
    repo = repo.expanduser().resolve()
    started = time.monotonic()
    invocation_recorded = _record_stage_invocation(repo, name)
    try:
        repeated = _repeat_failure_payload(repo, name)
        if repeated is not None:
            return repeated
        code, payload = _execute_stage_impl(
            name,
            repo,
            arguments=arguments,
            autodev_root=autodev_root,
            attempt=attempt,
            reason=reason,
            runner=runner,
            which=which,
        )
        payload["stage_elapsed_ms"] = int((time.monotonic() - started) * 1000)
        return code, payload
    finally:
        if not invocation_recorded:
            _record_stage_invocation(repo, name)
        _record_stage_timing(repo, name, int((time.monotonic() - started) * 1000))


def _execute_stage_impl(
    name: str,
    repo: Path,
    *,
    arguments: str,
    autodev_root: Path,
    attempt: int,
    reason: str,
    runner: Callable[..., object],
    which: Callable[[str], str | None],
) -> tuple[int, dict[str, object]]:
    autodev_root = autodev_root.expanduser().resolve()
    if name not in STAGES:
        raise WorkflowStageError(f"unsupported workflow stage: {name}")
    if attempt < 0:
        raise WorkflowStageError("workflow stage attempt must be zero or greater")

    if name == "preflight":
        _preflight(repo, arguments, which)
        return 0, stage_payload(
            repo,
            "CONTINUE",
            name,
            requested_issue=issue_number_from_arguments(arguments),
            next_action="prepare the requested issue",
        )

    if name == "prepare":
        current = ensure_prepared_issue(
            repo,
            arguments,
            autodev_root=autodev_root,
            runner=runner,
        )
        return 0, stage_payload(
            repo,
            "CONTINUE",
            name,
            next_action="delegate to autodev-reader",
            artifact=current / "state.json",
        )

    current = repo / CURRENT_DIR
    if name == "failed":
        state_value = read_json(current / "state.json")
        state = state_value if isinstance(state_value, dict) else {}
        if state:
            mark_blocked(current, state, reason or "OpenCode coordinator failed.", runner=runner)
        return 0, stage_payload(
            repo,
            "FAILED",
            name,
            reason=reason or "OpenCode coordinator failed",
            failure_classification=FAILURE_DETERMINISTIC,
            next_action="inspect the failure artifacts, correct the setup/provider/subagent failure, then restart intentionally",
        )

    state = read_state(current)

    if name == "render-implementer":
        _require_accepted_role(current, state, "planner", "plan.md")
        render_implementer_prompt(repo, current, state, autodev_root)
        return 0, stage_payload(
            repo,
            "CONTINUE",
            name,
            artifact=current / "implementer.md",
            next_action="delegate to autodev-implementer; implementer.md is already rendered and must not be prepared again",
        )

    if name == "local-check":
        _require_accepted_role(current, state, "implementer", "commit-message.txt")
        max_attempts = configured_attempt_limit(
            "MAX_REPAIR_ATTEMPTS",
            DEFAULT_MAX_REPAIR_ATTEMPTS,
        )
        passed = run_local_check(repo, current, state, autodev_root, runner=runner)
        if passed:
            return 0, stage_payload(
                repo,
                "CONTINUE",
                name,
                next_action="run semantic verification",
                max_repair_attempts=max_attempts,
            )
        if attempt >= max_attempts:
            return 0, stage_payload(
                repo,
                "BLOCKED",
                name,
                reason="deterministic repair-attempt limit exhausted",
                artifact=current / "local-repair.md",
                failure_classification=FAILURE_DETERMINISTIC,
                next_action="mark the run blocked",
                max_repair_attempts=max_attempts,
            )
        return 0, stage_payload(
            repo,
            "REPAIR",
            name,
            reason="deterministic verification failed",
            artifact=current / "local-repair.md",
            failure_classification=FAILURE_CODE_REPAIRABLE,
            next_action="delegate the local repair to autodev-fixer, increment the attempt, then rerun local-check",
            max_repair_attempts=max_attempts,
        )

    if name == "semantic":
        max_attempts = configured_attempt_limit(
            "MAX_SEMANTIC_REPAIR_ATTEMPTS",
            DEFAULT_MAX_SEMANTIC_REPAIR_ATTEMPTS,
        )
        result_path = current / "verification-result.json"
        if not result_path.is_file() or not read_text(result_path).strip():
            raise WorkflowStageError(
                "semantic prerequisite not met: .autodev-run/current/verification-result.json is missing; "
                "run the verifier role and accept its result before the semantic stage"
            )
        _require_accepted_role(current, state, "verifier", "verification-result.json")
        if state.get("VerificationProofVersion"):
            proof = source_identity(repo, current, state)
            if proof["identity"] != str(state.get("VerifiedSourceIdentity", "")):
                raise WorkflowStageError(
                    "semantic prerequisite not met: source changed after deterministic verification; rerun local-check"
                )
            if proof["parent_sha"] != str(state.get("VerifiedParentSha", "")):
                raise WorkflowStageError(
                    "semantic prerequisite not met: source parent changed after deterministic verification; rerun local-check"
                )
        issue_text = read_text(current / "issue.md") or str(state.get("IssueText", ""))
        result = parse_semantic_output(
            read_text(result_path),
            expected_criteria=extract_acceptance_criteria(issue_text) or None,
        )
        verdict = str(result["verdict"])
        state["LastSemanticVerdict"] = verdict
        if verdict == "pass" and state.get("VerificationProofVersion"):
            state["SemanticSourceIdentity"] = str(state.get("VerifiedSourceIdentity", ""))
        else:
            state.pop("SemanticSourceIdentity", None)
        write_state(current, state)
        if verdict == "pass":
            return 0, stage_payload(
                repo,
                "CONTINUE",
                name,
                next_action="run commit/push/PR/CI",
                max_semantic_repair_attempts=max_attempts,
            )
        if verdict == "blocked":
            return 0, stage_payload(
                repo,
                "BLOCKED",
                name,
                reason="semantic verifier blocked the run",
                artifact=result_path,
                failure_classification=FAILURE_DETERMINISTIC,
                next_action="mark the run blocked",
                max_semantic_repair_attempts=max_attempts,
            )
        if attempt >= max_attempts:
            return 0, stage_payload(
                repo,
                "BLOCKED",
                name,
                reason="semantic repair-attempt limit exhausted",
                artifact=result_path,
                failure_classification=FAILURE_DETERMINISTIC,
                next_action="mark the run blocked",
                max_semantic_repair_attempts=max_attempts,
            )
        repair_path = current / "verification-repair.md"
        prepare_semantic_repair_prompt(
            repo,
            current,
            autodev_root / "promptTemplates" / "semantic-repair.md",
            repair_path,
        )
        return 0, stage_payload(
            repo,
            "REPAIR",
            name,
            reason=str(result.get("repair_brief", "semantic repair requested")),
            artifact=repair_path,
            failure_classification=FAILURE_CODE_REPAIRABLE,
            next_action="delegate the semantic repair to autodev-fixer, increment the attempt, rerun local-check, then rerun autodev-verifier",
            max_semantic_repair_attempts=max_attempts,
        )

    if name == "pr-and-ci":
        if state.get("OpenCodeProtocolVersion"):
            if not bool(state.get("LastLocalCheckPassed")):
                raise WorkflowStageError(
                    "pr-and-ci prerequisite not met: deterministic local verification has not passed"
                )
            if str(state.get("LastSemanticVerdict", "")) != "pass":
                raise WorkflowStageError(
                    "pr-and-ci prerequisite not met: semantic verification has not produced an accepted pass verdict"
                )
        max_attempts = configured_attempt_limit(
            "MAX_REPAIR_ATTEMPTS",
            DEFAULT_MAX_REPAIR_ATTEMPTS,
        )
        ci_passed = pr_and_ci(repo, current, state, autodev_root, runner=runner)
        if ci_passed:
            return 0, stage_payload(
                repo,
                "CONTINUE",
                name,
                next_action="mark the PR ready for human review",
                max_repair_attempts=max_attempts,
            )
        if attempt >= max_attempts:
            return 0, stage_payload(
                repo,
                "BLOCKED",
                name,
                reason="CI repair-attempt limit exhausted",
                artifact=current / "ci-repair.md",
                failure_classification=FAILURE_DETERMINISTIC,
                next_action="mark the run blocked",
                max_repair_attempts=max_attempts,
            )
        return 0, stage_payload(
            repo,
            "REPAIR",
            name,
            reason="required PR checks failed",
            artifact=current / "ci-repair.md",
            failure_classification=FAILURE_CODE_REPAIRABLE,
            next_action="delegate the CI repair to autodev-fixer, increment the attempt, rerun local-check and semantic verification, then retry pr-and-ci",
            max_repair_attempts=max_attempts,
        )

    if name == "ready":
        if not str(state.get("PrUrl", "")).strip():
            raise WorkflowStageError("cannot mark ready because state.json has no PR URL")
        validate_ready_proof(current, state, runner=runner)
        mark_ready(current, state, runner=runner)
        return 0, stage_payload(
            repo,
            "PR_READY",
            name,
            next_action="human review; AutoDev never merges automatically",
        )

    if name == "blocked":
        mark_blocked(current, state, reason or "OpenCode coordinator blocked the run.", runner=runner)
        return 0, stage_payload(
            repo,
            "BLOCKED",
            name,
            reason=reason,
            failure_classification=FAILURE_DETERMINISTIC,
            next_action="inspect the current AutoDev artifacts and intervene manually",
        )

    status = str(state.get("Status", ""))
    outcome = "PR_READY" if status == "ReadyForReview" else "BLOCKED" if status == "Blocked" else "CONTINUE"
    return 0, stage_payload(
        repo,
        outcome,
        name,
        next_action="human review" if outcome == "PR_READY" else "continue from the current AutoDev stage",
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

    owner = os.environ.get("GITHUB_OWNER", "").strip()
    repo_name = os.environ.get("GITHUB_REPO", "").strip()
    if not owner or not repo_name:
        raise WorkflowStageError("GITHUB_OWNER and GITHUB_REPO are required to prepare an issue")
    repo_full = f"{owner}/{repo_name}"

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


def validate_prepared_worktree(
    repo: Path,
    base_sha: str,
    *,
    runner: Callable[..., object] = subprocess.run,
) -> str:
    head = _decoded_text(
        getattr(git(repo, ["rev-parse", "HEAD"], runner=runner), "stdout", "")
    ).strip()
    if not head:
        raise WorkflowStageError("could not resolve local HEAD while preparing the run")
    if head != base_sha:
        raise WorkflowStageError(
            f"prepared local HEAD {head} does not match remote base {base_sha}; update/switch to the exact base before starting AutoDev"
        )
    status = _decoded_text(
        getattr(
            git(
                repo,
                ["status", "--porcelain=v1", "--untracked-files=all"],
                runner=runner,
            ),
            "stdout",
            "",
        )
    )
    dirty = [path for path in _porcelain_paths(status) if not ignored_workspace_path(path)]
    if dirty:
        raise WorkflowStageError(
            "prepared worktree is not clean; AutoDev cannot bind verification to the remote base while these paths differ: "
            + ", ".join(dirty[:20])
        )
    return head


def resolve_profiles(
    labels: list[str],
    profiles_path: Path,
    *,
    explicit_profiles: str,
    explicit_local_check: str,
    explicit_stack_context: str,
    autodev_root: Path,
) -> tuple[str, str, str]:
    config = read_json(profiles_path)
    if not isinstance(config, dict):
        config = {}
    if not config and not explicit_local_check.strip():
        raise WorkflowStageError(
            f"verification profile configuration is missing or invalid: {profiles_path}; set LOCAL_CHECK explicitly"
        )
    definitions = config.get("profiles", {})
    definitions = definitions if isinstance(definitions, dict) else {}
    selected = [value for value in re.split(r"[,;\s]+", explicit_profiles.casefold()) if value]
    if not selected:
        for key, value in definitions.items():
            if not isinstance(value, dict):
                continue
            profile_labels = [str(item) for item in value.get("labels", [])]
            if any(label in labels for label in profile_labels):
                selected.append(str(key))
    if not selected:
        selected = [str(config.get("defaultProfile", "auto") or "auto")]
    selected = list(dict.fromkeys(selected))
    if "auto" in selected and len(selected) > 1:
        selected = [item for item in selected if item != "auto"]

    verify_profiles: list[str] = []
    contexts: list[str] = []
    for profile_name in selected:
        value = definitions.get(profile_name, {}) if profile_name != "auto" else {}
        value = value if isinstance(value, dict) else {}
        verify_profiles.append(str(value.get("verifyProfile", profile_name)))
        context = str(value.get("stackContext", "")).strip()
        if context:
            contexts.append(context)
    profiles_csv = ",".join(dict.fromkeys(verify_profiles))
    if explicit_local_check.strip():
        local_check = explicit_local_check.strip()
    else:
        template = str(config.get("verifyCommandTemplate", "")).strip()
        if not template:
            raise WorkflowStageError(
                f"verification profile {profiles_path} has no verifyCommandTemplate; set LOCAL_CHECK explicitly"
            )
        codex_tools = os.environ.get("CODEX_TOOLS_DIR", str(Path.home() / "codex-tools"))
        local_check = (
            template.replace("{{ProfilesCsv}}", profiles_csv)
            .replace("{{AutomationRoot}}", str(autodev_root))
            .replace("{{CodexToolsDir}}", codex_tools)
        )
    stack_context = explicit_stack_context.strip() or "\n".join(contexts)
    if not stack_context:
        stack_context = (
            "No specific area profile was selected. Use repository AGENTS.md, README, project files, "
            "solution/package files, and CI configuration as the source of truth. Prefer the smallest safe scope."
        )
    return profiles_csv, local_check, stack_context


def _preflight(repo: Path, arguments: str, which: Callable[[str], str | None]) -> None:
    if not repo.is_dir():
        raise WorkflowStageError(f"target repository is not a directory: {repo}")
    if not (repo / ".git").exists():
        raise WorkflowStageError(f"target repository is not a Git worktree: {repo}")
    missing = [tool for tool in ("git", "gh") if which(tool) is None]
    if missing:
        raise WorkflowStageError("required command is unavailable: " + ", ".join(missing))
    if not sys.executable:
        raise WorkflowStageError("Python executable is unavailable")
    if issue_number_from_arguments(arguments) == 0:
        raise WorkflowStageError("pass an issue number to /autodev-issue-to-pr")
    missing_config = [name for name in ("GITHUB_OWNER", "GITHUB_REPO") if not os.environ.get(name, "").strip()]
    if missing_config:
        raise WorkflowStageError("required AutoDev setting is unavailable: " + ", ".join(missing_config))
    configured_attempt_limit("MAX_REPAIR_ATTEMPTS", DEFAULT_MAX_REPAIR_ATTEMPTS)
    configured_attempt_limit("MAX_SEMANTIC_REPAIR_ATTEMPTS", DEFAULT_MAX_SEMANTIC_REPAIR_ATTEMPTS)
    configured_attempt_limit("CI_CHECK_POLL_ATTEMPTS", DEFAULT_CI_CHECK_POLL_ATTEMPTS)
    configured_nonnegative_float("CI_CHECK_POLL_SECONDS", DEFAULT_CI_CHECK_POLL_SECONDS)


def render_implementer_prompt(repo: Path, current: Path, state: dict[str, object], autodev_root: Path) -> None:
    plan = read_text(current / "plan.md")
    if not plan.strip():
        raise WorkflowStageError("cannot render implementer prompt because plan.md is missing")
    template = read_text(autodev_root / "promptTemplates" / "implementer.md")
    prompt = render_template(
        template,
        {
            "IssueText": read_text(current / "issue.md") or str(state.get("IssueText", "")),
            "Plan": plan,
            "LocalCheck": str(state.get("LocalCheck", "")),
            "StackContext": str(state.get("StackContext", "")),
        },
    )
    write_text(current / "implementer.md", prompt)
    state["Status"] = "ImplementerPromptRendered"
    write_state(current, state)


def run_local_check(
    repo: Path,
    current: Path,
    state: dict[str, object],
    autodev_root: Path,
    *,
    runner: Callable[..., object] = subprocess.run,
) -> bool:
    command = str(state.get("LocalCheck", "")).strip()
    if not command:
        raise WorkflowStageError("state.json has no LocalCheck command")
    completed = _run_captured(
        runner,
        command,
        cwd=repo,
        shell=True,
    )
    output = _decoded_text(getattr(completed, "stdout", "")) + _decoded_text(
        getattr(completed, "stderr", "")
    )
    write_text(current / "local-check.log", output)
    if int(getattr(completed, "returncode", 1)) == 0:
        state["Status"] = "LocalCheckPassed"
        state["LastLocalCheckPassed"] = True
        if state.get("VerificationProofVersion"):
            proof = source_identity(repo, current, state)
            state["VerifiedParentSha"] = proof["parent_sha"]
            state["VerifiedSourceIdentity"] = proof["identity"]
            state["VerifiedChanges"] = proof["changes"]
            state.pop("LastSemanticVerdict", None)
            state.pop("SemanticSourceIdentity", None)
            state.pop("CreatedCommitSha", None)
            state.pop("CreatedTreeSha", None)
            state.pop("CreatedParentSha", None)
            state.pop("ShippedSourceIdentity", None)
            state.pop("ShippedTreeVerified", None)
            state.pop("PrHeadSha", None)
            state.pop("CiProof", None)
            _record_shipment_diagnostics(
                current,
                verified_parent_sha=proof["parent_sha"],
                verified_source_identity=proof["identity"],
                verified_change_count=len(proof["changes"]),
            )
        write_state(current, state)
        return True

    template = read_text(autodev_root / "promptTemplates" / "local-repair.md")
    prompt = render_template(
        template,
        {
            "IssueText": read_text(current / "issue.md") or str(state.get("IssueText", "")),
            "FailureLog": output,
            "LocalCheck": command,
            "StackContext": str(state.get("StackContext", "")),
        },
    )
    write_text(current / "local-repair.md", prompt)
    state["Status"] = "LocalCheckFailed"
    state["LastLocalCheckPassed"] = False
    if state.get("VerificationProofVersion"):
        state.pop("VerifiedParentSha", None)
        state.pop("VerifiedSourceIdentity", None)
        state.pop("VerifiedChanges", None)
        state.pop("LastSemanticVerdict", None)
        state.pop("SemanticSourceIdentity", None)
        state.pop("CiProof", None)
    write_state(current, state)
    return False


def pr_and_ci(
    repo: Path,
    current: Path,
    state: dict[str, object],
    autodev_root: Path,
    *,
    runner: Callable[..., object] = subprocess.run,
) -> bool:
    changes = workspace_changes(repo, current, state)
    if changes:
        write_json(current / "changed-files.json", changes)
        commit_sha = create_api_commit(repo, state, changes, current, runner=runner)
        state = read_state(current)
        state["LastCommitSha"] = commit_sha
        state["Status"] = "CommittedViaGitHubApi"
        state.pop("CommitTreeBaseSha", None)
        write_state(current, state)
        snapshot_path = current / "last-commit-workspace-snapshot.json"
        write_workspace_snapshot(repo, snapshot_path)
        state = read_state(current)
        state["LastCommitSnapshotHash"] = _file_sha256(snapshot_path)
        write_state(current, state)
    elif not str(state.get("PrUrl", "")).strip():
        raise WorkflowStageError("no workspace file changes detected, and no PR exists")

    state = read_state(current)
    ensure_pr(repo, current, state, runner=runner)
    state = read_state(current)
    ci_proof = wait_for_required_checks(repo, state, runner=runner)
    write_json(current / "ci-summary.json", ci_proof)
    state = read_state(current)
    state["CiProof"] = ci_proof
    if ci_proof["state"] == "terminal-failure":
        render_ci_repair(current, state, autodev_root)
        state["Status"] = "CiFailed"
        write_state(current, state)
        return False
    if ci_proof["state"] != "terminal-success":
        raise WorkflowStageError(
            f"required CI did not reach terminal success for {ci_proof.get('head_sha', '')}: {ci_proof.get('state', '')}",
            classification=FAILURE_TRANSIENT,
        )

    render_legacy_verifier(repo, current, state, autodev_root, runner=runner)
    state["Status"] = "CiPassedVerifierPromptRendered"
    write_state(current, state)
    return True


def source_identity(repo: Path, current: Path, state: dict[str, object]) -> dict[str, object]:
    parent_sha = str(state.get("LastCommitSha", "")).strip() or str(state.get("BaseSha", "")).strip()
    if not parent_sha:
        raise WorkflowStageError("cannot calculate source identity because the current commit parent is missing")
    baseline_path, expected_hash = _baseline_snapshot(current, state)
    baseline = read_json(baseline_path)
    if not isinstance(baseline, dict):
        raise WorkflowStageError(f"workspace snapshot is missing or invalid: {baseline_path}")
    if expected_hash and _file_sha256(baseline_path) != expected_hash:
        raise WorkflowStageError(
            f"workspace baseline proof changed unexpectedly: {baseline_path}"
        )
    actual = workspace_snapshot(repo)
    changes: list[dict[str, str]] = []
    for path, digest in actual.items():
        if path not in baseline:
            changes.append({"path": path, "status": "added", "sha256": digest})
        elif str(baseline[path]) != digest:
            changes.append({"path": path, "status": "modified", "sha256": digest})
    for path in baseline:
        if path not in actual:
            changes.append({"path": str(path), "status": "deleted", "sha256": ""})
    changes.sort(key=lambda item: item["path"])
    identity = hashlib.sha256(
        json.dumps(
            {"parent_sha": parent_sha, "changes": changes},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8", errors="replace")
    ).hexdigest()
    return {"parent_sha": parent_sha, "identity": identity, "changes": changes}


def create_api_commit(
    repo: Path,
    state: dict[str, object],
    changes: list[dict[str, str]],
    current: Path,
    *,
    runner: Callable[..., object] = subprocess.run,
) -> str:
    repo_full = str(state.get("RepoFullName", ""))
    branch = str(state.get("BranchName", ""))
    base_sha = str(state.get("BaseSha", "")).strip()
    parent = str(state.get("LastCommitSha", "")).strip() or base_sha
    if not repo_full or not branch or not parent:
        raise WorkflowStageError("state.json is missing repository/branch/base commit information")

    verified_proof: dict[str, object] | None = None
    if state.get("VerificationProofVersion"):
        if not bool(state.get("LastLocalCheckPassed")):
            raise WorkflowStageError("API commit refused because local verification is not current")
        verified_proof = source_identity(repo, current, state)
        expected_identity = str(state.get("VerifiedSourceIdentity", ""))
        expected_parent = str(state.get("VerifiedParentSha", ""))
        if not expected_identity or verified_proof["identity"] != expected_identity:
            raise WorkflowStageError(
                "API commit refused because the workspace no longer matches the source state that passed local verification"
            )
        if not expected_parent or verified_proof["parent_sha"] != expected_parent or parent != expected_parent:
            raise WorkflowStageError(
                "API commit refused because the commit parent no longer matches the parent used for local verification"
            )
        supplied = sorted(
            ({"path": str(item["Path"]), "status": str(item["Status"])} for item in changes),
            key=lambda item: item["path"],
        )
        verified_paths = [
            {"path": str(item["path"]), "status": str(item["status"])}
            for item in verified_proof["changes"]
            if isinstance(item, dict)
        ]
        if supplied != verified_paths:
            raise WorkflowStageError(
                "API commit refused because the changed-file set differs from the locally verified source identity"
            )

    base_tree = ""
    if parent == base_sha:
        base_tree = str(state.get("BaseTreeSha", "")).strip()
    if not base_tree:
        parent_commit = gh_json(repo, ["api", f"repos/{repo_full}/git/commits/{parent}"], runner=runner)
        tree = parent_commit.get("tree", {})
        base_tree = str(tree.get("sha", "")) if isinstance(tree, dict) else ""
        if not base_tree:
            raise WorkflowStageError(
                f"could not resolve base tree for API commit parent {parent}; GitHub response: {_json_evidence(parent_commit)}"
            )
        if parent == base_sha:
            state["BaseTreeSha"] = base_tree
            write_state(current, state)
    if not base_tree:
        raise WorkflowStageError(f"could not resolve base tree for API commit parent {parent}")

    verified_by_path: dict[str, dict[str, str]] = {}
    if verified_proof is not None:
        verified_by_path = {
            str(item["path"]): item
            for item in verified_proof["changes"]
            if isinstance(item, dict)
        }

    tree_items: list[dict[str, object]] = []
    for change in changes:
        relative = str(change["Path"])
        if change["Status"] == "deleted":
            tree_items.append({"path": relative, "mode": "100644", "type": "blob", "sha": None})
            continue
        path = repo / relative
        if not path.is_file():
            raise WorkflowStageError(f"changed file does not exist: {path}")
        content = path.read_bytes()
        if verified_by_path:
            actual_digest = hashlib.sha256(content).hexdigest().upper()
            expected_digest = str(verified_by_path.get(relative, {}).get("sha256", ""))
            if not expected_digest or actual_digest != expected_digest:
                raise WorkflowStageError(
                    f"API commit refused because {relative} changed after local verification"
                )
        blob = gh_json(
            repo,
            ["api", f"repos/{repo_full}/git/blobs", "--method", "POST", "--input", "-"],
            input_text=json.dumps(
                {
                    "content": base64.b64encode(content).decode("ascii"),
                    "encoding": "base64",
                }
            ),
            runner=runner,
        )
        blob_sha = str(blob.get("sha", ""))
        if not blob_sha:
            raise WorkflowStageError(
                f"GitHub API did not return a blob SHA for {relative}; response: {_json_evidence(blob)}"
            )
        tree_items.append({"path": relative, "mode": "100644", "type": "blob", "sha": blob_sha})

    tree = gh_json(
        repo,
        ["api", f"repos/{repo_full}/git/trees", "--method", "POST", "--input", "-"],
        input_text=json.dumps({"base_tree": base_tree, "tree": tree_items}),
        runner=runner,
    )
    tree_sha = str(tree.get("sha", ""))
    if not tree_sha:
        raise WorkflowStageError(
            f"GitHub API did not return a tree SHA; response: {_json_evidence(tree)}"
        )
    message = commit_message(current, state)
    commit = gh_json(
        repo,
        ["api", f"repos/{repo_full}/git/commits", "--method", "POST", "--input", "-"],
        input_text=json.dumps({"message": message, "tree": tree_sha, "parents": [parent]}),
        runner=runner,
    )
    sha = str(commit.get("sha", ""))
    if not sha:
        raise WorkflowStageError(
            f"GitHub API did not return a commit SHA; response: {_json_evidence(commit)}"
        )

    if state.get("VerificationProofVersion"):
        created = gh_json(repo, ["api", f"repos/{repo_full}/git/commits/{sha}"], runner=runner)
        created_tree = created.get("tree", {})
        created_tree_sha = str(created_tree.get("sha", "")) if isinstance(created_tree, dict) else ""
        created_parents = created.get("parents", [])
        created_parent = ""
        if isinstance(created_parents, list) and created_parents and isinstance(created_parents[0], dict):
            created_parent = str(created_parents[0].get("sha", ""))
        if created_tree_sha != tree_sha or created_parent != parent:
            raise WorkflowStageError(
                "GitHub commit proof did not match the locally verified shipment: "
                f"expected tree={tree_sha} parent={parent}; got tree={created_tree_sha or '<missing>'} "
                f"parent={created_parent or '<missing>'}; response={_json_evidence(created)}"
            )
        state["CreatedCommitSha"] = sha
        state["CreatedTreeSha"] = tree_sha
        state["CreatedParentSha"] = parent
        state["ShippedSourceIdentity"] = str(verified_proof["identity"] if verified_proof else "")
        state["ShippedTreeVerified"] = True
        write_state(current, state)
        _record_shipment_diagnostics(
            current,
            created_commit_sha=sha,
            created_tree_sha=tree_sha,
            created_parent_sha=parent,
            shipped_source_identity=state["ShippedSourceIdentity"],
        )

    ref_path = f"heads/{branch}"
    existing = gh(repo, ["api", f"repos/{repo_full}/git/ref/{ref_path}"], runner=runner, check=False)
    if int(getattr(existing, "returncode", 1)) == 0:
        gh(
            repo,
            ["api", f"repos/{repo_full}/git/refs/{ref_path}", "--method", "PATCH", "--input", "-"],
            input_text=json.dumps({"sha": sha, "force": False}),
            runner=runner,
        )
    else:
        gh(
            repo,
            ["api", f"repos/{repo_full}/git/refs", "--method", "POST", "--input", "-"],
            input_text=json.dumps({"ref": f"refs/heads/{branch}", "sha": sha}),
            runner=runner,
        )
    return sha


def ensure_pr(
    repo: Path,
    current: Path,
    state: dict[str, object],
    *,
    runner: Callable[..., object] = subprocess.run,
) -> None:
    repo_full = str(state.get("RepoFullName", ""))
    pr_url = str(state.get("PrUrl", "")).strip()
    if not pr_url:
        body = (
            "Implements:\n\n"
            + (read_text(current / "issue.md") or str(state.get("IssueText", "")))
            + "\nAutoDev plan:\n\n"
            + read_text(current / "plan.md")
            + "\nLocal verification:\n\n```text\n"
            + str(state.get("LocalCheck", ""))
            + "\n```\n"
        )
        body_path = current / "pr-body.md"
        write_text(body_path, body)
        completed = gh(
            repo,
            [
                "pr",
                "create",
                "--repo",
                repo_full,
                "--base",
                str(state.get("Base", "main")),
                "--head",
                str(state.get("BranchName", "")),
                "--title",
                str(state.get("IssueTitle", "AutoDev change")),
                "--body-file",
                str(body_path),
            ],
            runner=runner,
        )
        lines = [line.strip() for line in _decoded_text(getattr(completed, "stdout", "")).splitlines() if line.strip()]
        pr_url = lines[-1] if lines else ""
        if not pr_url:
            raise WorkflowStageError(
                f"gh pr create did not return a PR URL: {_command_reason(completed)}"
            )
        state["PrUrl"] = pr_url

    selector = str(state.get("PrNumber", 0) or 0) or pr_url
    details = gh_json(
        repo,
        ["pr", "view", selector, "--repo", repo_full, "--json", "number,headRefOid"],
        runner=runner,
    )
    state["PrNumber"] = int(details.get("number", 0) or 0)
    head_sha = str(details.get("headRefOid", "")).strip()
    if state.get("VerificationProofVersion"):
        expected = str(state.get("LastCommitSha", "")).strip()
        if not expected or not head_sha:
            raise WorkflowStageError("PR head proof is missing the expected/current head SHA")
        if head_sha != expected:
            raise WorkflowStageError(
                f"PR head {head_sha} does not match the exact AutoDev commit {expected}"
            )
    state["PrHeadSha"] = head_sha
    write_state(current, state)
    _record_shipment_diagnostics(current, pr_head_sha=head_sha)


def wait_for_required_checks(
    repo: Path,
    state: dict[str, object],
    *,
    runner: Callable[..., object] = subprocess.run,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, object]:
    repo_full = str(state.get("RepoFullName", ""))
    pr_number = int(state.get("PrNumber", 0) or 0)
    expected_head = str(state.get("LastCommitSha", "")).strip()
    if pr_number <= 0:
        raise WorkflowStageError("state.json has no PR number")
    if not expected_head:
        raise WorkflowStageError("state.json has no commit SHA to bind CI checks to")

    attempts = configured_attempt_limit("CI_CHECK_POLL_ATTEMPTS", DEFAULT_CI_CHECK_POLL_ATTEMPTS)
    if attempts <= 0:
        attempts = 1
    interval = configured_nonnegative_float("CI_CHECK_POLL_SECONDS", DEFAULT_CI_CHECK_POLL_SECONDS)
    current = repo / CURRENT_DIR
    last_proof: dict[str, object] = {
        "head_sha": expected_head,
        "state": "not-observed",
        "checks": [],
        "polls": 0,
        "required_only": True,
    }

    for poll in range(1, attempts + 1):
        head_before = _pr_head_sha(repo, repo_full, pr_number, runner=runner)
        if head_before != expected_head:
            raise WorkflowStageError(
                f"required CI is attached to PR head {head_before or '<missing>'}, not expected AutoDev commit {expected_head}"
            )

        checks = _query_pr_checks(repo, repo_full, pr_number, required=True, runner=runner)
        required_only = True
        if not checks:
            checks = _query_pr_checks(repo, repo_full, pr_number, required=False, runner=runner)
            required_only = False

        head_after = _pr_head_sha(repo, repo_full, pr_number, runner=runner)
        if head_after != expected_head:
            raise WorkflowStageError(
                f"PR head changed while CI was being observed: expected {expected_head}, got {head_after or '<missing>'}"
            )

        state_name = _ci_state(checks)
        last_proof = {
            "head_sha": expected_head,
            "state": state_name,
            "checks": checks,
            "polls": poll,
            "required_only": required_only,
        }
        _persist_ci_proof(current, last_proof)
        if state_name in {"terminal-success", "terminal-failure"}:
            return last_proof
        if poll < attempts and interval:
            sleep(interval)

    raise WorkflowStageError(
        f"required CI for exact PR head {expected_head} did not reach terminal state after {attempts} polls; last state={last_proof['state']}",
        classification=FAILURE_TRANSIENT,
    )


def _query_pr_checks(
    repo: Path,
    repo_full: str,
    pr_number: int,
    *,
    required: bool,
    runner: Callable[..., object],
) -> list[dict[str, object]]:
    arguments = [
        "pr",
        "checks",
        str(pr_number),
        "--repo",
        repo_full,
    ]
    if required:
        arguments.append("--required")
    arguments.extend(["--json", "name,bucket,state,description,link"])
    completed = gh(repo, arguments, runner=runner, check=False)
    text = _decoded_text(getattr(completed, "stdout", "")).strip()
    stderr = _decoded_text(getattr(completed, "stderr", "")).strip()
    if "\ufffd" in text:
        raise WorkflowStageError("gh pr checks returned invalid UTF-8 JSON output")
    if not text:
        if int(getattr(completed, "returncode", 1)) != 0:
            lowered = stderr.casefold()
            if "no checks" not in lowered and "no required" not in lowered:
                raise WorkflowStageError(
                    _command_reason(completed),
                    classification=_command_failure_classification(completed),
                )
        return []
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise WorkflowStageError(
            f"gh pr checks returned invalid JSON: {concise(text, 700)}"
        ) from exc
    if not isinstance(value, list):
        raise WorkflowStageError(
            f"gh pr checks returned an unexpected JSON value: {concise(text, 700)}"
        )
    return [item for item in value if isinstance(item, dict)]


def _ci_state(checks: list[dict[str, object]]) -> str:
    if not checks:
        return "not-observed"
    buckets = [str(item.get("bucket", "")).strip().casefold() for item in checks]
    if any(bucket == "pending" for bucket in buckets):
        return "queued/in-progress"
    if all(bucket == "pass" for bucket in buckets):
        return "terminal-success"
    return "terminal-failure"


def _persist_ci_proof(current: Path, proof: dict[str, object]) -> None:
    write_json(current / "ci-summary.json", proof)
    state = read_state(current)
    state["CiProof"] = proof
    write_state(current, state)
    _record_shipment_diagnostics(
        current,
        ci_state=proof.get("state", ""),
        ci_head_sha=proof.get("head_sha", ""),
        ci_polls=proof.get("polls", 0),
        ci_checks=[
            {
                "name": str(item.get("name", "")),
                "bucket": str(item.get("bucket", "")),
                "state": str(item.get("state", "")),
            }
            for item in proof.get("checks", [])
            if isinstance(item, dict)
        ],
    )


def _pr_head_sha(
    repo: Path,
    repo_full: str,
    pr_number: int,
    *,
    runner: Callable[..., object],
) -> str:
    details = gh_json(
        repo,
        ["pr", "view", str(pr_number), "--repo", repo_full, "--json", "number,headRefOid"],
        runner=runner,
    )
    return str(details.get("headRefOid", "")).strip()


def render_ci_repair(current: Path, state: dict[str, object], autodev_root: Path) -> None:
    prompt = render_template(
        read_text(autodev_root / "promptTemplates" / "ci-repair.md"),
        {
            "IssueText": read_text(current / "issue.md") or str(state.get("IssueText", "")),
            "Plan": read_text(current / "plan.md"),
            "CiSummary": read_text(current / "ci-summary.json"),
            "LocalCheck": str(state.get("LocalCheck", "")),
            "StackContext": str(state.get("StackContext", "")),
        },
    )
    write_text(current / "ci-repair.md", prompt)


def render_legacy_verifier(
    repo: Path,
    current: Path,
    state: dict[str, object],
    autodev_root: Path,
    *,
    runner: Callable[..., object] = subprocess.run,
) -> None:
    repo_full = str(state.get("RepoFullName", ""))
    pr_number = int(state.get("PrNumber", 0) or 0)
    completed = gh(repo, ["pr", "diff", str(pr_number), "--repo", repo_full], runner=runner, check=False)
    diff = _decoded_text(getattr(completed, "stdout", ""))
    prompt = render_template(
        read_text(autodev_root / "promptTemplates" / "verifier.md"),
        {
            "IssueText": read_text(current / "issue.md") or str(state.get("IssueText", "")),
            "Plan": read_text(current / "plan.md"),
            "Diff": diff,
            # The shared verifier template uses the presence of these literals to
            # select its legacy PASS/FAIL contract. Identity substitutions keep
            # them literal under the collision-safe one-pass renderer.
            "AcceptanceCriteria": "{{AcceptanceCriteria}}",
            "SynthesizedHandoff": "{{SynthesizedHandoff}}",
            "ChangedFiles": "{{ChangedFiles}}",
            "DeterministicEvidence": "{{DeterministicEvidence}}",
            "CrossFileRegressionEvidence": "{{CrossFileRegressionEvidence}}",
            "UncertaintyNotes": "{{UncertaintyNotes}}",
            "LocalCheck": str(state.get("LocalCheck", "")),
            "StackContext": str(state.get("StackContext", "")),
        },
    )
    write_text(current / "verifier.md", prompt)


def validate_ready_proof(
    current: Path,
    state: dict[str, object],
    *,
    runner: Callable[..., object] = subprocess.run,
) -> None:
    if not state.get("VerificationProofVersion"):
        return
    repo = current.parents[1]
    repo_full = str(state.get("RepoFullName", ""))
    commit_sha = str(state.get("LastCommitSha", "")).strip()
    created_sha = str(state.get("CreatedCommitSha", "")).strip()
    created_tree = str(state.get("CreatedTreeSha", "")).strip()
    created_parent = str(state.get("CreatedParentSha", "")).strip()
    verified_parent = str(state.get("VerifiedParentSha", "")).strip()
    verified_identity = str(state.get("VerifiedSourceIdentity", "")).strip()
    shipped_identity = str(state.get("ShippedSourceIdentity", "")).strip()
    if not commit_sha or created_sha != commit_sha:
        raise WorkflowStageError("ready refused: durable state does not prove the current AutoDev commit")
    if not created_tree or not created_parent or not bool(state.get("ShippedTreeVerified")):
        raise WorkflowStageError("ready refused: shipped commit/tree proof is missing")
    if not verified_identity or shipped_identity != verified_identity or verified_parent != created_parent:
        raise WorkflowStageError("ready refused: shipped tree is not bound to the source identity that passed local verification")
    if not bool(state.get("LastLocalCheckPassed")):
        raise WorkflowStageError("ready refused: deterministic local verification is not current")

    semantic_required = bool(state.get("OpenCodeProtocolVersion")) or "LastSemanticVerdict" in state
    if semantic_required:
        if str(state.get("LastSemanticVerdict", "")) != "pass":
            raise WorkflowStageError("ready refused: semantic verification has not passed")
        if str(state.get("SemanticSourceIdentity", "")) != shipped_identity:
            raise WorkflowStageError("ready refused: semantic verification does not apply to the shipped source identity")

    ci_proof = state.get("CiProof", {})
    if not isinstance(ci_proof, dict):
        raise WorkflowStageError("ready refused: CI proof is missing")
    checks = ci_proof.get("checks", [])
    if (
        ci_proof.get("state") != "terminal-success"
        or str(ci_proof.get("head_sha", "")) != commit_sha
        or not isinstance(checks, list)
        or not checks
        or any(str(item.get("bucket", "")).casefold() != "pass" for item in checks if isinstance(item, dict))
    ):
        raise WorkflowStageError("ready refused: required CI is not terminal-success for the exact current PR head")

    pr_number = int(state.get("PrNumber", 0) or 0)
    if pr_number <= 0 or not str(state.get("PrUrl", "")).strip():
        raise WorkflowStageError("ready refused: PR proof is missing")
    current_head = _pr_head_sha(repo, repo_full, pr_number, runner=runner)
    if current_head != commit_sha:
        raise WorkflowStageError(
            f"ready refused: current PR head {current_head or '<missing>'} differs from verified AutoDev commit {commit_sha}"
        )
    commit = gh_json(repo, ["api", f"repos/{repo_full}/git/commits/{commit_sha}"], runner=runner)
    tree = commit.get("tree", {})
    actual_tree = str(tree.get("sha", "")) if isinstance(tree, dict) else ""
    parents = commit.get("parents", [])
    actual_parent = ""
    if isinstance(parents, list) and parents and isinstance(parents[0], dict):
        actual_parent = str(parents[0].get("sha", ""))
    if actual_tree != created_tree or actual_parent != created_parent:
        raise WorkflowStageError(
            "ready refused: GitHub commit tree/parent no longer matches the durable shipped-tree proof"
        )


def mark_ready(
    current: Path,
    state: dict[str, object],
    *,
    runner: Callable[..., object] = subprocess.run,
) -> None:
    issue_number = int(state.get("IssueNumber", 0) or 0)
    repo_full = str(state.get("RepoFullName", ""))
    repo = current.parents[1]
    if issue_number:
        gh(
            repo,
            [
                "issue",
                "edit",
                str(issue_number),
                "--repo",
                repo_full,
                "--remove-label",
                "autodev:running",
                "--remove-label",
                "autodev:blocked",
                "--add-label",
                "autodev:done",
            ],
            runner=runner,
        )
        gh(
            repo,
            [
                "issue",
                "comment",
                str(issue_number),
                "--repo",
                repo_full,
                "--body",
                f"AutoDev automation completed.\n\nPR:\n{state.get('PrUrl', '')}\n\nStatus:\nReady for review/merge.",
            ],
            runner=runner,
        )
    state["Status"] = "ReadyForReview"
    write_state(current, state)


def mark_blocked(
    current: Path,
    state: dict[str, object],
    reason: str,
    *,
    runner: Callable[..., object] = subprocess.run,
) -> None:
    issue_number = int(state.get("IssueNumber", 0) or 0)
    repo_full = str(state.get("RepoFullName", ""))
    repo = current.parents[1]
    if issue_number:
        gh(
            repo,
            [
                "issue",
                "edit",
                str(issue_number),
                "--repo",
                repo_full,
                "--remove-label",
                "autodev:running",
                "--add-label",
                "autodev:blocked",
            ],
            runner=runner,
        )
        gh(
            repo,
            [
                "issue",
                "comment",
                str(issue_number),
                "--repo",
                repo_full,
                "--body",
                f"AutoDev automation blocked.\n\nReason:\n\n```text\n{reason}\n```",
            ],
            runner=runner,
        )
    state["Status"] = "Blocked"
    write_state(current, state)


def workspace_changes(repo: Path, current: Path, state: dict[str, object]) -> list[dict[str, str]]:
    baseline_path, expected_hash = _baseline_snapshot(current, state)
    baseline = read_json(baseline_path)
    if not isinstance(baseline, dict):
        raise WorkflowStageError(f"workspace snapshot is missing or invalid: {baseline_path}")
    if state.get("VerificationProofVersion") and expected_hash and _file_sha256(baseline_path) != expected_hash:
        raise WorkflowStageError(f"workspace snapshot proof changed unexpectedly: {baseline_path}")
    actual = workspace_snapshot(repo)
    changes: list[dict[str, str]] = []
    for path, digest in actual.items():
        if path not in baseline:
            changes.append({"Path": path, "Status": "added"})
        elif str(baseline[path]) != digest:
            changes.append({"Path": path, "Status": "modified"})
    for path in baseline:
        if path not in actual:
            changes.append({"Path": str(path), "Status": "deleted"})
    return sorted(changes, key=lambda item: item["Path"])


def _baseline_snapshot(current: Path, state: dict[str, object]) -> tuple[Path, str]:
    if str(state.get("LastCommitSha", "")).strip():
        path = current / "last-commit-workspace-snapshot.json"
        if state.get("VerificationProofVersion") and not path.is_file():
            raise WorkflowStageError("last committed workspace snapshot is missing; cannot bind repair verification to its parent")
        return path, str(state.get("LastCommitSnapshotHash", ""))
    return current / "workspace-snapshot.json", str(state.get("PreparedSnapshotHash", ""))


def workspace_snapshot(repo: Path) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    for path in repo.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(repo).as_posix()
        if ignored_workspace_path(relative):
            continue
        try:
            snapshot[relative] = hashlib.sha256(path.read_bytes()).hexdigest().upper()
        except OSError:
            continue
    return snapshot


def write_workspace_snapshot(repo: Path, path: Path) -> None:
    write_json(path, workspace_snapshot(repo))


def ignored_workspace_path(relative: str) -> bool:
    normalized = relative.replace("\\", "/").removeprefix("./")
    return normalized == "memory.md" or normalized.endswith("/memory.md") or any(
        normalized.startswith(prefix) or f"/{prefix}" in f"/{normalized}"
        for prefix in IGNORED_PREFIXES
    )


def stage_payload(
    repo: Path,
    outcome: str,
    stage: str,
    *,
    reason: str = "",
    artifact: Path | None = None,
    requested_issue: int = 0,
    next_action: str = "",
    max_repair_attempts: int | None = None,
    max_semantic_repair_attempts: int | None = None,
    failure_classification: str = "",
    failure_fingerprint: str = "",
    repeated_failure: bool = False,
) -> dict[str, object]:
    current = repo / CURRENT_DIR
    state_value = read_json(current / "state.json")
    state = state_value if isinstance(state_value, dict) else {}
    ci_proof = state.get("CiProof", {})
    ci_state = str(ci_proof.get("state", "")) if isinstance(ci_proof, dict) else ""
    payload: dict[str, object] = {
        "state": outcome,
        "issue_number": int(state.get("IssueNumber", 0) or requested_issue or 0),
        "branch": str(state.get("BranchName", "")),
        "completed_stage": str(state.get("Status", "")),
        "failed_stage": stage if outcome in {"FAILED", "BLOCKED", "REPAIR"} else "",
        "stage": stage,
        "reason": concise(reason),
        "failure_classification": failure_classification,
        "failure_fingerprint": failure_fingerprint,
        "repeated_failure": repeated_failure,
        "artifact_dir": str(current),
        "artifact": str(artifact) if artifact is not None else "",
        "repository_modified": repository_modified(repo, current, state),
        "commit_exists": bool(str(state.get("LastCommitSha", "")).strip()),
        "pr_exists": bool(str(state.get("PrUrl", "")).strip()),
        "pr_url": str(state.get("PrUrl", "")),
        "pr_head_sha": str(state.get("PrHeadSha", "")),
        "verified_source_identity": str(state.get("VerifiedSourceIdentity", "")),
        "created_commit_sha": str(state.get("CreatedCommitSha", "")),
        "created_tree_sha": str(state.get("CreatedTreeSha", "")),
        "ci_state": ci_state,
        "next_action": next_action,
    }
    diagnostics = read_json(current / DIAGNOSTICS_FILE)
    if isinstance(diagnostics, dict):
        payload["diagnostics"] = {
            "role_invocations": diagnostics.get("role_invocations", {}),
            "protocol_correction_attempts": diagnostics.get("protocol_correction_attempts", {}),
            "stage_invocations": diagnostics.get("stage_invocations", {}),
            "repeated_identical_failures": diagnostics.get("repeated_identical_failures", 0),
            "stage_wall_time_ms": diagnostics.get("stage_wall_time_ms", {}),
            "shipment_proof": diagnostics.get("shipment_proof", {}),
        }
    if max_repair_attempts is not None:
        payload["max_repair_attempts"] = max_repair_attempts
    if max_semantic_repair_attempts is not None:
        payload["max_semantic_repair_attempts"] = max_semantic_repair_attempts
    return payload


def record_stage_failure(
    repo: Path,
    stage: str,
    error: BaseException,
    *,
    requested_issue: int = 0,
    next_action: str = "correct the reported setup or deterministic stage failure before retrying",
) -> dict[str, object]:
    repo = repo.expanduser().resolve()
    classification = _exception_classification(error)
    reason = concise(str(error))
    input_fingerprint = _stage_input_fingerprint(repo, stage)
    fingerprint = hashlib.sha256(
        f"{stage}|{classification}|{reason}|{input_fingerprint}".encode("utf-8", errors="replace")
    ).hexdigest()
    current = repo / CURRENT_DIR
    if current.is_dir():
        diagnostics = _diagnostics(current)
        failures = diagnostics.setdefault("failure_fingerprints", {})
        if isinstance(failures, dict):
            failures[fingerprint] = int(failures.get(fingerprint, 0) or 0) + 1
        diagnostics["last_failure"] = {
            "stage": stage,
            "classification": classification,
            "reason": reason,
            "fingerprint": fingerprint,
            "input_fingerprint": input_fingerprint,
        }
        _write_diagnostics(current, diagnostics)
    return stage_payload(
        repo,
        "FAILED",
        stage,
        reason=reason,
        requested_issue=requested_issue,
        next_action=next_action,
        failure_classification=classification,
        failure_fingerprint=fingerprint,
    )


def repository_modified(repo: Path, current: Path, state: dict[str, object]) -> bool:
    try:
        return bool(workspace_changes(repo, current, state))
    except (OSError, WorkflowStageError):
        return False


def configured_attempt_limit(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise WorkflowStageError(f"{name} must be an integer") from exc
    if value < 0:
        raise WorkflowStageError(f"{name} must be zero or greater")
    return value


def configured_nonnegative_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise WorkflowStageError(f"{name} must be a number") from exc
    if value < 0:
        raise WorkflowStageError(f"{name} must be zero or greater")
    return value


def commit_message(current: Path, state: dict[str, object]) -> str:
    lines = read_text(current / "commit-message.txt").splitlines()
    if lines and lines[0].strip():
        return lines[0].strip()[:200]
    number = int(state.get("IssueNumber", 0) or 0)
    title = str(state.get("IssueTitle", "")).strip()
    return f"Implement issue-{number}: {title}" if title else f"Implement issue-{number} via AutoDev"


def safe_slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9._-]+", "-", value.casefold()).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    return slug[:120] or "issue"


def gh(
    repo: Path,
    arguments: list[str],
    *,
    input_text: str | None = None,
    runner: Callable[..., object] = subprocess.run,
    check: bool = True,
):
    completed = _run_captured(
        runner,
        ["gh", *arguments],
        cwd=repo,
        input_text=input_text,
        env=_gh_environment(),
    )
    if check and int(getattr(completed, "returncode", 1)) != 0:
        raise WorkflowStageError(
            _command_reason(completed),
            classification=_command_failure_classification(completed),
        )
    return completed


def git(
    repo: Path,
    arguments: list[str],
    *,
    runner: Callable[..., object] = subprocess.run,
    check: bool = True,
):
    completed = _run_captured(
        runner,
        ["git", *arguments],
        cwd=repo,
    )
    if check and int(getattr(completed, "returncode", 1)) != 0:
        raise WorkflowStageError(_command_reason(completed))
    return completed


def gh_json(
    repo: Path,
    arguments: list[str],
    *,
    input_text: str | None = None,
    runner: Callable[..., object] = subprocess.run,
) -> dict[str, object]:
    completed = gh(repo, arguments, input_text=input_text, runner=runner)
    text = _decoded_text(getattr(completed, "stdout", "")).strip()
    if "\ufffd" in text:
        raise WorkflowStageError(
            f"gh returned invalid JSON for {' '.join(arguments)}: output contained invalid UTF-8 bytes: {concise(text, 700)}"
        )
    try:
        value = json.loads(text or "{}")
    except json.JSONDecodeError as exc:
        raise WorkflowStageError(
            f"gh returned invalid JSON for {' '.join(arguments)}: {concise(text, 700)}"
        ) from exc
    if not isinstance(value, dict):
        raise WorkflowStageError(
            f"gh returned an unexpected JSON value for {' '.join(arguments)}: {concise(text, 700)}"
        )
    return value


def _run_captured(
    runner: Callable[..., object],
    command: object,
    *,
    cwd: Path,
    shell: bool = False,
    input_text: str | None = None,
    env: dict[str, str] | None = None,
):
    kwargs: dict[str, object] = {
        "cwd": cwd,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "capture_output": True,
        "check": False,
    }
    if shell:
        kwargs["shell"] = True
    if input_text is not None:
        kwargs["input"] = input_text
    if env is not None:
        kwargs["env"] = env
    return runner(command, **kwargs)


def _decoded_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _gh_environment() -> dict[str, str]:
    env = dict(os.environ)
    env["GH_PROMPT_DISABLED"] = "1"
    return env


def _command_reason(completed: object) -> str:
    stderr = _decoded_text(getattr(completed, "stderr", ""))
    stdout = _decoded_text(getattr(completed, "stdout", ""))
    code = int(getattr(completed, "returncode", 1))
    evidence = (stderr or stdout or "no command output").strip()
    return concise(f"command exited with {code}: {evidence}")


def _command_failure_classification(completed: object) -> str:
    text = (
        _decoded_text(getattr(completed, "stderr", ""))
        + " "
        + _decoded_text(getattr(completed, "stdout", ""))
    ).casefold()
    transient_markers = (
        "timed out",
        "timeout",
        "temporarily unavailable",
        "connection reset",
        "connection refused",
        "network",
        "rate limit",
        "http 429",
        "http 500",
        "http 502",
        "http 503",
        "http 504",
    )
    return FAILURE_TRANSIENT if any(marker in text for marker in transient_markers) else FAILURE_DETERMINISTIC


def _exception_classification(error: BaseException) -> str:
    classification = str(getattr(error, "classification", "") or "")
    if classification in {FAILURE_CODE_REPAIRABLE, FAILURE_TRANSIENT, FAILURE_DETERMINISTIC}:
        return classification
    if classification in {"rate_limited", "timeout", "network_error", "provider_unavailable"}:
        return FAILURE_TRANSIENT
    return FAILURE_DETERMINISTIC


def _require_accepted_role(
    current: Path,
    state: dict[str, object],
    role: str,
    artifact_name: str,
) -> None:
    if not state.get("OpenCodeProtocolVersion"):
        return
    accepted = state.get("AcceptedRoleArtifacts", {})
    entry = accepted.get(role) if isinstance(accepted, dict) else None
    if not isinstance(entry, dict):
        raise WorkflowStageError(
            f"stage prerequisite not met: OpenCode role {role} has not been accepted; "
            f"accept {artifact_name} before continuing"
        )
    artifact = current / artifact_name
    expected = str(entry.get("sha256", ""))
    actual = _file_sha256(artifact)
    if not expected or not actual or expected != actual:
        raise WorkflowStageError(
            f"stage prerequisite not met: accepted {role} artifact {artifact_name} is missing or changed; "
            "rerun the role's exact accept command before continuing"
        )


def _repeat_failure_payload(repo: Path, stage: str) -> tuple[int, dict[str, object]] | None:
    if stage in {"preflight", "prepare", "failed", "blocked", "ready", "status"}:
        return None
    current = repo / CURRENT_DIR
    if not current.is_dir():
        return None
    diagnostics = _diagnostics(current)
    last = diagnostics.get("last_failure", {})
    if not isinstance(last, dict):
        return None
    if last.get("stage") != stage or last.get("classification") != FAILURE_DETERMINISTIC:
        return None
    current_input = _stage_input_fingerprint(repo, stage)
    if not current_input or current_input != str(last.get("input_fingerprint", "")):
        return None
    diagnostics["repeated_identical_failures"] = int(
        diagnostics.get("repeated_identical_failures", 0) or 0
    ) + 1
    fingerprint = str(last.get("fingerprint", ""))
    _write_diagnostics(current, diagnostics)
    return 1, stage_payload(
        repo,
        "FAILED",
        stage,
        reason=str(last.get("reason", "identical deterministic stage failure")),
        failure_classification=FAILURE_DETERMINISTIC,
        failure_fingerprint=fingerprint,
        repeated_failure=True,
        next_action="do not retry this stage unchanged; correct the deterministic workflow/setup state first",
    )


def _stage_input_fingerprint(repo: Path, stage: str) -> str:
    current = repo / CURRENT_DIR
    state_value = read_json(current / "state.json")
    state = state_value if isinstance(state_value, dict) else {}
    if not state and not current.exists():
        return ""
    state_keys = (
        "IssueNumber",
        "Status",
        "BranchName",
        "BaseSha",
        "BaseTreeSha",
        "PreparedSnapshotHash",
        "LastCommitSha",
        "LastCommitSnapshotHash",
        "VerifiedParentSha",
        "VerifiedSourceIdentity",
        "CreatedCommitSha",
        "CreatedTreeSha",
        "PrHeadSha",
        "CiProof",
        "PrUrl",
        "PrNumber",
        "LocalCheck",
        "LastLocalCheckPassed",
        "LastSemanticVerdict",
        "SemanticSourceIdentity",
        "OpenCodeProtocolVersion",
    )
    artifacts = {}
    for name in (
        "issue.md",
        "plan.md",
        "implementer.md",
        "commit-message.txt",
        "verification-result.json",
        "local-repair.md",
        "verification-repair.md",
        "ci-repair.md",
    ):
        digest = _file_sha256(current / name)
        if digest:
            artifacts[name] = digest
    payload = {
        "stage": stage,
        "state": {key: state.get(key) for key in state_keys},
        "accepted_roles": state.get("AcceptedRoleArtifacts", {}),
        "artifacts": artifacts,
        "workspace": workspace_snapshot(repo) if repo.is_dir() else {},
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8", errors="replace")
    ).hexdigest()


def _record_stage_invocation(repo: Path, stage: str) -> bool:
    current = repo / CURRENT_DIR
    if not current.is_dir():
        return False
    diagnostics = _diagnostics(current)
    values = diagnostics.setdefault("stage_invocations", {})
    if isinstance(values, dict):
        values[stage] = int(values.get(stage, 0) or 0) + 1
    _write_diagnostics(current, diagnostics)
    return True


def _record_stage_timing(repo: Path, stage: str, elapsed_ms: int) -> None:
    current = repo / CURRENT_DIR
    if not current.is_dir():
        return
    diagnostics = _diagnostics(current)
    values = diagnostics.setdefault("stage_wall_time_ms", {})
    if isinstance(values, dict):
        entries = values.setdefault(stage, [])
        if isinstance(entries, list):
            entries.append(max(0, elapsed_ms))
    _write_diagnostics(current, diagnostics)


def _record_shipment_diagnostics(current: Path, **values: object) -> None:
    diagnostics = _diagnostics(current)
    shipment = diagnostics.setdefault("shipment_proof", {})
    if isinstance(shipment, dict):
        shipment.update(values)
    _write_diagnostics(current, diagnostics)


def _diagnostics(current: Path) -> dict[str, object]:
    value = read_json(current / DIAGNOSTICS_FILE)
    return value if isinstance(value, dict) else {}


def _write_diagnostics(current: Path, diagnostics: dict[str, object]) -> None:
    try:
        write_json(current / DIAGNOSTICS_FILE, diagnostics)
    except OSError:
        pass


def _file_sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


def _json_evidence(value: object) -> str:
    try:
        return concise(json.dumps(value, ensure_ascii=False, sort_keys=True), 900)
    except (TypeError, ValueError):
        return concise(str(value), 900)


def _porcelain_paths(value: str) -> list[str]:
    paths: list[str] = []
    for line in value.splitlines():
        if len(line) < 4:
            continue
        path = line[3:].strip().strip('"')
        if " -> " in path:
            path = path.rsplit(" -> ", 1)[-1]
        if path:
            paths.append(path.replace("\\", "/"))
    return paths


def concise(value: str, limit: int = 1000) -> str:
    return " ".join(str(value).split())[:limit]


def read_state(current: Path) -> dict[str, object]:
    state = read_json(current / "state.json")
    if not isinstance(state, dict) or not state:
        raise WorkflowStageError(".autodev-run/current/state.json is missing or invalid")
    return state


def write_state(current: Path, state: dict[str, object]) -> None:
    write_json(current / "state.json", state)


def read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def write_json(path: Path, value: object) -> None:
    write_text(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Portable AutoDev non-model workflow stages.")
    parser.add_argument("stage", choices=STAGES)
    parser.add_argument("--repo", default=".")
    parser.add_argument("--arguments", default="")
    parser.add_argument("--autodev-root", default=str(AUTODEV_ROOT))
    parser.add_argument("--attempt", type=int, default=0)
    parser.add_argument("--reason", default="")
    return parser


def run(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo = Path(args.repo).expanduser().resolve()
    try:
        code, payload = execute_stage(
            args.stage,
            repo,
            arguments=args.arguments,
            autodev_root=Path(args.autodev_root),
            attempt=args.attempt,
            reason=args.reason,
        )
    except (WorkflowStageError, SemanticVerifierError, OSError, ValueError) as exc:
        payload = record_stage_failure(
            repo,
            args.stage,
            exc,
            requested_issue=issue_number_from_arguments(args.arguments),
        )
        print(json.dumps(payload, sort_keys=True))
        return 1
    print(json.dumps(payload, sort_keys=True))
    return code


def main() -> int:
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
