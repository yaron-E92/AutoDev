from __future__ import annotations

import json
import os
import shutil
import subprocess
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator

from automation import cli_help, run_manifest, workflow_contract, workflow_storage, workflow_workspace
from automation.workflow_commands import _decoded_text, _porcelain_paths, git


CONTINUE_FROM_ENV = "AUTODEV_CONTINUE_FROM"
CONTINUE_FROM_SHA_ENV = "AUTODEV_CONTINUE_FROM_SHA"
SCHEMA_VERSION = 1
CONTINUATIONS_DIR = "continuations"


class ContinuationError(ValueError):
    pass


def register_help() -> None:
    option = (
        "--continue-from REF",
        "Adopt implementation bytes from REF without changing the configured development/PR base.",
    )
    issue = cli_help.HELP.get(("issue-to-pr",))
    if issue is not None and option[0] not in {name for name, _ in issue.options}:
        cli_help.HELP[("issue-to-pr",)] = replace(
            issue,
            usage=issue.usage.replace(
                " [--semver INTENT]",
                " [--semver INTENT] [--continue-from REF]",
            ),
            description=(
                issue.description
                + " `--continue-from` adopts an existing branch/tag/commit as immutable implementation input while preserving repository development policy and PR targeting."
            ),
            options=(*issue.options, option),
            examples=(*issue.examples, "autodev issue-to-pr 123 --continue-from feature/existing-work"),
        )
    resume = cli_help.HELP.get(("resume",))
    if resume is not None and option[0] not in {name for name, _ in resume.options}:
        cli_help.HELP[("resume",)] = replace(
            resume,
            usage=resume.usage.replace("]", " [--continue-from REF]]"),
            description=(
                resume.description
                + " `--continue-from` explicitly replaces the run's implementation source, archives the previous source checkpoint, invalidates stale role/verification evidence, and keeps the configured PR base unchanged."
            ),
            options=(*resume.options, option),
            examples=(*resume.examples, "autodev resume --continue-from recovered-work"),
        )


def consume_public_args(values: list[str]) -> tuple[list[str], str, str]:
    if not values or values[0] not in {"issue-to-pr", "resume"}:
        return values, "", ""
    cleaned = [values[0]]
    requested = ""
    index = 1
    while index < len(values):
        value = values[index]
        if value != "--continue-from":
            cleaned.append(value)
            index += 1
            continue
        if requested:
            return values, "", "--continue-from may be specified only once"
        if index + 1 >= len(values) or not values[index + 1].strip():
            return values, "", "--continue-from requires a branch, tag, or commit ref"
        requested = values[index + 1].strip()
        if requested.startswith("-"):
            return values, "", "--continue-from ref must not begin with '-'"
        index += 2
    return cleaned, requested, ""


def repo_from_args(values: list[str]) -> Path:
    repo = Path(".")
    for index, value in enumerate(values):
        if value == "--repo" and index + 1 < len(values):
            repo = Path(values[index + 1])
            break
    return repo.expanduser().resolve()


def _stdout(completed: object) -> str:
    return _decoded_text(getattr(completed, "stdout", "")).strip()


def resolve_ref(
    repo: Path,
    requested_ref: str,
    *,
    runner: Callable[..., object] = subprocess.run,
) -> str:
    repo = repo.expanduser().resolve()
    requested = str(requested_ref or "").strip()
    if not requested:
        raise ContinuationError("continuation ref is empty")
    if requested.startswith("-"):
        raise ContinuationError("continuation ref must not begin with '-'")
    candidates = [requested]
    if not requested.startswith("refs/"):
        candidates.extend(
            (
                f"refs/heads/{requested}",
                f"refs/remotes/origin/{requested}",
                f"refs/tags/{requested}",
            )
        )
    resolved: list[str] = []
    for candidate in candidates:
        completed = git(
            repo,
            ["rev-parse", "--verify", f"{candidate}^{{commit}}"],
            runner=runner,
            check=False,
        )
        if int(getattr(completed, "returncode", 1)) == 0:
            sha = _stdout(completed)
            if sha and sha not in resolved:
                resolved.append(sha)
    if not resolved:
        raise ContinuationError(
            f"cannot resolve continuation ref {requested!r} to a local commit; fetch the branch/tag or provide a resolvable commit SHA"
        )
    if len(resolved) > 1:
        raise ContinuationError(
            f"continuation ref {requested!r} is ambiguous across local refs; use a fully qualified ref or full commit SHA"
        )
    return resolved[0]


def _dirty_paths(
    repo: Path,
    *,
    runner: Callable[..., object] = subprocess.run,
) -> list[str]:
    completed = git(
        repo,
        ["status", "--porcelain=v1", "--untracked-files=all"],
        runner=runner,
    )
    return [
        path
        for path in _porcelain_paths(_decoded_text(getattr(completed, "stdout", "")))
        if not workflow_workspace.ignored_workspace_path(path)
    ]


def _checkout_detached(
    repo: Path,
    resolved_sha: str,
    *,
    runner: Callable[..., object] = subprocess.run,
) -> None:
    dirty = _dirty_paths(repo, runner=runner)
    if dirty:
        raise ContinuationError(
            "cannot adopt continuation source with a dirty worktree; commit/stash the current changes first: "
            + ", ".join(dirty[:20])
        )
    head = _stdout(git(repo, ["rev-parse", "HEAD"], runner=runner))
    if head == resolved_sha:
        return
    completed = git(
        repo,
        ["checkout", "--detach", resolved_sha],
        runner=runner,
        check=False,
    )
    if int(getattr(completed, "returncode", 1)) != 0:
        detail = _decoded_text(getattr(completed, "stderr", "")) or _decoded_text(
            getattr(completed, "stdout", "")
        )
        raise ContinuationError(
            f"cannot check out continuation commit {resolved_sha}: {detail.strip() or 'git checkout failed'}"
        )


def _require_policy_ancestry(
    repo: Path,
    base_sha: str,
    continuation_sha: str,
    *,
    runner: Callable[..., object] = subprocess.run,
) -> None:
    if base_sha == continuation_sha:
        return
    completed = git(
        repo,
        ["merge-base", "--is-ancestor", base_sha, continuation_sha],
        runner=runner,
        check=False,
    )
    code = int(getattr(completed, "returncode", 1))
    if code == 0:
        return
    if code == 1:
        raise ContinuationError(
            "continuation source is not descended from the configured development base; rebase/cherry-pick the recovered work onto the configured integration branch before continuing so AutoDev does not retarget or distort the PR relationship"
        )
    detail = _decoded_text(getattr(completed, "stderr", "")) or _decoded_text(
        getattr(completed, "stdout", "")
    )
    raise ContinuationError(
        f"cannot validate continuation ancestry between {base_sha} and {continuation_sha}: {detail.strip() or 'git merge-base failed'}"
    )


@contextmanager
def new_run_scope(
    repo: Path,
    requested_ref: str,
    *,
    runner: Callable[..., object] = subprocess.run,
) -> Iterator[str]:
    if not requested_ref:
        yield ""
        return
    resolved = resolve_ref(repo, requested_ref, runner=runner)
    old_ref = os.environ.get(CONTINUE_FROM_ENV)
    old_sha = os.environ.get(CONTINUE_FROM_SHA_ENV)
    os.environ[CONTINUE_FROM_ENV] = requested_ref
    os.environ[CONTINUE_FROM_SHA_ENV] = resolved
    try:
        yield resolved
    finally:
        if old_ref is None:
            os.environ.pop(CONTINUE_FROM_ENV, None)
        else:
            os.environ[CONTINUE_FROM_ENV] = old_ref
        if old_sha is None:
            os.environ.pop(CONTINUE_FROM_SHA_ENV, None)
        else:
            os.environ[CONTINUE_FROM_SHA_ENV] = old_sha


def _clear_downstream_state(state: dict[str, object]) -> None:
    accepted = state.get("AcceptedRoleArtifacts", {})
    if isinstance(accepted, dict):
        for role in ("reader", "synthesizer", "planner", "implementer", "fixer", "verifier"):
            accepted.pop(role, None)
        state["AcceptedRoleArtifacts"] = accepted
    state["Status"] = "Prepared"
    state["LastCommitSha"] = ""
    state["LastLocalCheckPassed"] = False
    state["PrUrl"] = ""
    state["PrNumber"] = 0
    state["PrHeadSha"] = ""
    state["CiProof"] = {}
    for key in (
        "VerifiedParentSha",
        "VerifiedSourceIdentity",
        "ShippedSourceIdentity",
        "SemanticSourceIdentity",
        "LastCommitSnapshotHash",
        "CreatedCommitSha",
        "CreatedTreeSha",
        "CreatedParentSha",
        "LastSemanticVerdict",
    ):
        state.pop(key, None)
    state["VerifiedChanges"] = []


def _archive_existing(current: Path, continuation_id: str) -> Path:
    root = current / CONTINUATIONS_DIR / continuation_id
    root.mkdir(parents=True, exist_ok=False)
    for source, name in (
        (current / "state.json", "state-before.json"),
        (current / run_manifest.MANIFEST_NAME, "run-manifest-before.json"),
        (current / "workspace-snapshot.json", "workspace-snapshot-before.json"),
    ):
        if source.is_file():
            shutil.copy2(source, root / name)
    return root


def _continuation_record(requested_ref: str, resolved_sha: str) -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "requested_ref": requested_ref,
        "resolved_sha": resolved_sha,
        "adopted_at": datetime.now(timezone.utc).isoformat(),
    }


def adopt_existing_run(
    repo: Path,
    requested_ref: str,
    *,
    runner: Callable[..., object] = subprocess.run,
) -> dict[str, object]:
    repo = repo.expanduser().resolve()
    current = repo / workflow_contract.CURRENT_DIR
    manifest_path = current / run_manifest.MANIFEST_NAME
    state_path = current / "state.json"
    if not current.is_dir() or not manifest_path.is_file() or not state_path.is_file():
        raise ContinuationError("--continue-from on resume requires an existing durable AutoDev run")
    active_revision_path = current / "revision.json"
    if active_revision_path.is_file():
        try:
            active_revision = json.loads(active_revision_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            active_revision = {}
        if isinstance(active_revision, dict) and str(active_revision.get("status", "")) == "active":
            raise ContinuationError(
                "cannot replace the implementation source while an operator revision is active; resume/finish that revision first, then continue-from or start a new revision afterward"
            )

    state_value = workflow_storage.read_json(state_path)
    state = state_value if isinstance(state_value, dict) else {}
    if not state:
        raise ContinuationError("current run state.json is missing or invalid")
    base_sha = str(state.get("BaseSha", "")).strip()
    if not base_sha:
        raise ContinuationError("current run is missing its configured development-base SHA")

    resolved = resolve_ref(repo, requested_ref, runner=runner)
    _require_policy_ancestry(repo, base_sha, resolved, runner=runner)
    _checkout_detached(repo, resolved, runner=runner)

    continuation_id = datetime.now(timezone.utc).strftime("c%Y%m%dT%H%M%SZ")
    archive = _archive_existing(current, continuation_id)
    workflow_workspace.write_workspace_snapshot(repo, current / "workspace-snapshot.json")
    snapshot_hash = workflow_storage._file_sha256(current / "workspace-snapshot.json")

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    previous_branch = str(state.get("BranchName", "autodev/continued")).strip() or "autodev/continued"
    state["BranchName"] = f"{previous_branch}-continuation-{timestamp}"
    state["PreparedLocalHeadSha"] = resolved
    state["PreparedSnapshotHash"] = snapshot_hash
    state["ContinuationSourceVersion"] = SCHEMA_VERSION
    state["ContinuationRequestedRef"] = requested_ref
    state["ContinuationResolvedSha"] = resolved
    state["ContinuationArchive"] = str(archive.relative_to(current))
    state["ContinuationAdoptedAt"] = datetime.now(timezone.utc).isoformat()
    _clear_downstream_state(state)
    workflow_storage.write_json(state_path, state)

    try:
        run_manifest.invalidate_role(
            manifest_path,
            "reader",
            reason=f"operator continuation source replaced by {requested_ref} -> {resolved}",
        )
        manifest = run_manifest.load_manifest(manifest_path)
        target = manifest.get("target", {})
        if not isinstance(target, dict):
            target = {}
        target["branch"] = state["BranchName"]
        manifest["target"] = target
        manifest["continuation_source"] = _continuation_record(requested_ref, resolved)
        run_manifest.save_manifest(manifest_path, manifest)
    except run_manifest.ManifestError as exc:
        raise ContinuationError(str(exc)) from exc
    return _continuation_record(requested_ref, resolved)


def _bind_new_run(repo: Path, current: Path) -> None:
    requested = os.environ.get(CONTINUE_FROM_ENV, "").strip()
    resolved = os.environ.get(CONTINUE_FROM_SHA_ENV, "").strip()
    if not requested or not resolved:
        return
    state_value = workflow_storage.read_json(current / "state.json")
    state = state_value if isinstance(state_value, dict) else {}
    state["ContinuationSourceVersion"] = SCHEMA_VERSION
    state["ContinuationRequestedRef"] = requested
    state["ContinuationResolvedSha"] = resolved
    state["ContinuationAdoptedAt"] = datetime.now(timezone.utc).isoformat()
    workflow_storage.write_json(current / "state.json", state)


def _bind_manifest(repo: Path, path: Path) -> None:
    current = repo / workflow_contract.CURRENT_DIR
    state_value = workflow_storage.read_json(current / "state.json")
    state = state_value if isinstance(state_value, dict) else {}
    requested = str(state.get("ContinuationRequestedRef", "")).strip()
    resolved = str(state.get("ContinuationResolvedSha", "")).strip()
    if not requested or not resolved:
        return
    manifest = run_manifest.load_manifest(path)
    manifest["continuation_source"] = _continuation_record(requested, resolved)
    run_manifest.save_manifest(path, manifest)


def _continuation_sha(state: dict[str, object]) -> str:
    if str(state.get("LastCommitSha", "")).strip():
        return ""
    return str(state.get("ContinuationResolvedSha", "")).strip()


def install_hooks() -> None:
    from automation import (
        opencode_resume_manifest,
        opencode_resume_status,
        role_resume,
        workflow_dispatch,
        workflow_github,
        workflow_preparation,
        workflow_stages,
        workflow_verification,
    )

    current_validate = workflow_preparation.validate_prepared_worktree
    if not getattr(current_validate, "_autodev_continuation", False):
        original_validate = current_validate

        def validate_prepared_worktree(repo: Path, base_sha: str, **kwargs):
            requested = os.environ.get(CONTINUE_FROM_ENV, "").strip()
            resolved = os.environ.get(CONTINUE_FROM_SHA_ENV, "").strip()
            if not requested or not resolved:
                return original_validate(repo, base_sha, **kwargs)
            runner = kwargs.get("runner", subprocess.run)
            _require_policy_ancestry(repo, base_sha, resolved, runner=runner)
            _checkout_detached(repo, resolved, runner=runner)
            return resolved

        validate_prepared_worktree._autodev_continuation = True  # type: ignore[attr-defined]
        workflow_preparation.validate_prepared_worktree = validate_prepared_worktree

    current_prepare = workflow_dispatch.ensure_prepared_issue
    if not getattr(current_prepare, "_autodev_continuation", False):
        original_prepare = current_prepare

        def ensure_prepared_issue(repo: Path, arguments: str, **kwargs):
            requested = os.environ.get(CONTINUE_FROM_ENV, "").strip()
            resolved = os.environ.get(CONTINUE_FROM_SHA_ENV, "").strip()
            current = repo.expanduser().resolve() / workflow_contract.CURRENT_DIR
            if requested and current.is_dir():
                existing = workflow_storage.read_json(current / "state.json")
                issue = workflow_contract.issue_number_from_arguments(arguments)
                if isinstance(existing, dict) and int(existing.get("IssueNumber", 0) or 0) == issue:
                    existing_sha = str(existing.get("ContinuationResolvedSha", "")).strip()
                    if existing_sha == resolved:
                        return original_prepare(repo, arguments, **kwargs)
                    raise workflow_contract.WorkflowStageError(
                        "this issue already has a durable run; use `autodev resume --continue-from <ref>` to explicitly replace its implementation source"
                    )
            result = original_prepare(repo, arguments, **kwargs)
            _bind_new_run(repo.expanduser().resolve(), result)
            return result

        ensure_prepared_issue._autodev_continuation = True  # type: ignore[attr-defined]
        workflow_dispatch.ensure_prepared_issue = ensure_prepared_issue

    for module in (workflow_workspace, workflow_dispatch, workflow_github, workflow_stages):
        current_source = getattr(module, "source_identity", None)
        if current_source is None or getattr(current_source, "_autodev_continuation", False):
            continue
        original_source = current_source

        def source_identity(repo: Path, current: Path, state: dict[str, object], _original=original_source):
            continuation_sha = _continuation_sha(state)
            if not continuation_sha:
                return _original(repo, current, state)
            shadow = dict(state)
            shadow["BaseSha"] = continuation_sha
            return _original(repo, current, shadow)

        source_identity._autodev_continuation = True  # type: ignore[attr-defined]
        setattr(module, "source_identity", source_identity)

    for module in (workflow_github, workflow_verification, workflow_stages):
        current_commit = getattr(module, "create_api_commit", None)
        if current_commit is None or getattr(current_commit, "_autodev_continuation", False):
            continue
        original_commit = current_commit

        def create_api_commit(repo, state, changes, current, _original=original_commit, **kwargs):
            continuation_sha = _continuation_sha(state)
            if not continuation_sha:
                return _original(repo, state, changes, current, **kwargs)
            policy_base = str(state.get("BaseSha", ""))
            policy_tree = str(state.get("BaseTreeSha", ""))
            shadow = dict(state)
            shadow["BaseSha"] = continuation_sha
            shadow["BaseTreeSha"] = ""
            sha = _original(repo, shadow, changes, current, **kwargs)
            persisted = workflow_storage.read_json(current / "state.json")
            persisted_state = persisted if isinstance(persisted, dict) else {}
            persisted_state["BaseSha"] = policy_base
            persisted_state["BaseTreeSha"] = policy_tree
            workflow_storage.write_json(current / "state.json", persisted_state)
            return sha

        create_api_commit._autodev_continuation = True  # type: ignore[attr-defined]
        setattr(module, "create_api_commit", create_api_commit)

    for module in (role_resume, opencode_resume_manifest):
        current_create = getattr(module, "create_manifest", None)
        if current_create is None or getattr(current_create, "_autodev_continuation", False):
            continue
        original_create = current_create

        def create_manifest(repo: Path, state: dict[str, object], _original=original_create, **kwargs):
            path = _original(repo, state, **kwargs)
            _bind_manifest(repo.expanduser().resolve(), path)
            return path

        create_manifest._autodev_continuation = True  # type: ignore[attr-defined]
        setattr(module, "create_manifest", create_manifest)

    current_problems = opencode_resume_status._resume_problems
    if not getattr(current_problems, "_autodev_continuation", False):
        original_problems = current_problems

        def resume_problems(repo: Path, current: Path, manifest, state, **kwargs):
            problems = list(original_problems(repo, current, manifest, state, **kwargs))
            continuation_sha = _continuation_sha(state)
            if not continuation_sha or run_manifest.stage_completed(manifest, "patch-applied"):
                return problems
            base_sha = str(state.get("BaseSha", "")).strip()
            problems = [
                problem
                for problem in problems
                if problem != f"local HEAD {continuation_sha} no longer matches prepared base {base_sha}"
            ]
            runner = kwargs.get("runner", subprocess.run)
            head = _stdout(git(repo, ["rev-parse", "HEAD"], runner=runner))
            if head != continuation_sha:
                problems.append(
                    f"continuation source drift detected: expected local HEAD {continuation_sha}, got {head or '<missing>'}"
                )
            return problems

        resume_problems._autodev_continuation = True  # type: ignore[attr-defined]
        opencode_resume_status._resume_problems = resume_problems

    current_status = opencode_resume_status.status_text
    if not getattr(current_status, "_autodev_continuation", False):
        original_status = current_status

        def status_text(repo: Path, mappings, **kwargs):
            text = original_status(repo, mappings, **kwargs).rstrip("\n")
            current = repo.expanduser().resolve() / workflow_contract.CURRENT_DIR
            state_value = workflow_storage.read_json(current / "state.json")
            state = state_value if isinstance(state_value, dict) else {}
            requested = str(state.get("ContinuationRequestedRef", "")).strip()
            resolved = str(state.get("ContinuationResolvedSha", "")).strip()
            if not requested or not resolved:
                return text + "\n"
            extra = (
                f"Continuation source: {requested}\n"
                f"Resolved continuation SHA: {resolved}\n"
                f"Development base: {state.get('Base', '')}\n"
                f"PR target: {state.get('Base', '')}"
            )
            return text + "\n" + extra + "\n"

        status_text._autodev_continuation = True  # type: ignore[attr-defined]
        opencode_resume_status.status_text = status_text
