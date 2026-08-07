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

_ACTIVE_ROLES: ContextVar[dict[str, ModelConfig | None] | None] = ContextVar("active_roles", default=None)
_ACTIVE_FACTORY: ContextVar[Callable[[ModelConfig], ModelProvider] | None] = ContextVar("active_factory", default=None)
_ACTIVE_POLICIES: ContextVar[dict[str, str] | None] = ContextVar("active_policies", default=None)
_ACTIVE_SEMANTIC: ContextVar[SemanticSettings | None] = ContextVar("active_semantic", default=None)
_ACTIVE_DEBUG_ARTIFACTS: ContextVar[bool] = ContextVar("active_debug_artifacts", default=False)
_ACTIVE_ARGS: ContextVar[object | None] = ContextVar("active_args", default=None)
_ACTIVE_MANIFEST: ContextVar[Path | None] = ContextVar("active_manifest", default=None)
_ACTIVE_RESUMING: ContextVar[bool] = ContextVar("active_resuming", default=False)
_ACTIVE_ROLE_SNAPSHOTS: ContextVar[dict[str, object] | None] = ContextVar("active_role_snapshots", default=None)
_CORE_WRITE_OPERATIONAL_OUTPUTS = _core.write_operational_outputs
_CORE_SELECT_ISSUE = _core.select_issue
_CORE_FETCH_ISSUE_TEXT = _core.fetch_issue_text
_CORE_ENSURE_CLEAN_WORKTREE = _core.ensure_clean_worktree
_CORE_ENSURE_ISSUE_BRANCH = _core.ensure_issue_branch
_CORE_CREATE_DRAFT_PR = _core.create_draft_pr
_CORE_RUN_IMPLEMENTATION_LOOP = _core.run_implementation_loop


class _DeferredProvider(ModelProvider):
    def __init__(self, config: ModelConfig, factory: Callable[[ModelConfig], ModelProvider]):
        self.config = config
        self.factory = factory
        self.provider: ModelProvider | None = None

    def invoke(self, prompt: str, *, model: str, timeout_seconds: int) -> ProviderResponse:
        if self.provider is None:
            self.provider = self.factory(self.config)
        return self.provider.invoke(prompt, model=model, timeout_seconds=timeout_seconds)

    def generate(self, prompt: str, *, model: str, timeout_seconds: int) -> str:
        return self.invoke(prompt, model=model, timeout_seconds=timeout_seconds).text


def run(argv=None, *, stdout=None, stderr=None, provider_factory=None):
    out_stream = stdout if stdout is not None else sys.stdout
    err_stream = stderr if stderr is not None else sys.stderr
    raw_values = list(argv if argv is not None else sys.argv[1:])
    try:
        values, resume_dir, status_only, invalidated_roles = _extract_resume_options(raw_values)
        resume_manifest = manifest_path(resume_dir) if resume_dir is not None else None
        if status_only:
            if resume_manifest is None:
                raise ManifestError("--status requires --resume <run-directory>")
            manifest = load_manifest(resume_manifest)
            problems = validate_artifacts(manifest, resume_dir)
            print(
                render_status(
                    manifest,
                    requested_invalidations=sorted(invalidated_roles),
                    artifact_problems=problems,
                ),
                end="",
                file=out_stream,
            )
            return 0 if not problems else 2
        if resume_manifest is not None:
            manifest = load_manifest(resume_manifest)
            values = _inject_resume_arguments(values, resume_dir, manifest)
        args = _core.parse_args(values)
    except (ManifestError, RunnerError, SystemExit) as exc:
        message = str(exc)
        if message:
            print(message, file=err_stream)
        return 2

    try:
        roles = resolve_role_provider_configs(args)
        policies = resolve_prompt_policy_configs(args)
        semantic = resolve_semantic_verification_settings(args, roles)
        role_snapshots = _build_role_snapshots(roles, policies)
        if resume_manifest is not None:
            reconcile_role_snapshots(
                resume_manifest,
                role_snapshots,
                explicit_invalidations=invalidated_roles,
            )
            _update_resume_target_options(resume_manifest, args)
            _validate_next_stage_provider(load_manifest(resume_manifest), roles)
    except (ManifestError, ProviderError, RunnerError, OSError, json.JSONDecodeError) as exc:
        print(str(exc), file=err_stream)
        return exc.exit_code if isinstance(exc, RunnerError) else 2

    actual_factory = provider_factory or create_provider
    role_token = _ACTIVE_ROLES.set(roles)
    factory_token = _ACTIVE_FACTORY.set(actual_factory)
    policy_token = _ACTIVE_POLICIES.set(policies)
    semantic_token = _ACTIVE_SEMANTIC.set(semantic)
    debug_token = _ACTIVE_DEBUG_ARTIFACTS.set(bool(args.debug_artifacts))
    args_token = _ACTIVE_ARGS.set(args)
    manifest_token = _ACTIVE_MANIFEST.set(resume_manifest or manifest_path(Path(args.out).expanduser().resolve()))
    resume_token = _ACTIVE_RESUMING.set(resume_manifest is not None)
    snapshots_token = _ACTIVE_ROLE_SNAPSHOTS.set(role_snapshots)
    originals = {
        "resolve_provider_configs": _core.resolve_provider_configs,
        "run_area_reader": _core.run_area_reader,
        "write_operational_outputs": _core.write_operational_outputs,
        "run_implementation_loop": _core.run_implementation_loop,
        "write_provider_metadata": _core.write_provider_metadata,
        "create_draft_pr": _core.create_draft_pr,
        "build_pr_body": _core.build_pr_body,
    }
    for name in ("require_tools", "select_issue", "fetch_issue_text", "ensure_clean_worktree", "ensure_issue_branch"):
        originals[name] = getattr(_core, name)
        setattr(_core, name, globals()[name])
    try:
        _core.resolve_provider_configs = resolve_provider_configs
        _core.run_area_reader = run_area_reader
        _core.write_operational_outputs = write_operational_outputs
        _core.run_implementation_loop = run_implementation_loop
        _core.write_provider_metadata = write_provider_metadata
        _core.create_draft_pr = create_draft_pr
        _core.build_pr_body = build_pr_body
        result = _core.run(
            values,
            stdout=stdout,
            stderr=stderr,
            provider_factory=lambda config: _DeferredProvider(config, actual_factory),
        )
        manifest_file = _ACTIVE_MANIFEST.get()
        if manifest_file is not None and manifest_file.is_file():
            _sync_manifest_invocations(manifest_file.parent)
            if result != 0:
                manifest = load_manifest(manifest_file)
                if not manifest.get("failure"):
                    record_failure(
                        manifest_file,
                        classification="runner_failed",
                        reason=f"runner exited with code {result}",
                    )
        return result
    finally:
        for name, value in originals.items():
            setattr(_core, name, value)
        _ACTIVE_ROLES.reset(role_token)
        _ACTIVE_FACTORY.reset(factory_token)
        _ACTIVE_POLICIES.reset(policy_token)
        _ACTIVE_SEMANTIC.reset(semantic_token)
        _ACTIVE_DEBUG_ARTIFACTS.reset(debug_token)
        _ACTIVE_ARGS.reset(args_token)
        _ACTIVE_MANIFEST.reset(manifest_token)
        _ACTIVE_RESUMING.reset(resume_token)
        _ACTIVE_ROLE_SNAPSHOTS.reset(snapshots_token)


def main(argv=None):
    return run(argv)


def _extract_resume_options(values: list[str]) -> tuple[list[str], Path | None, bool, set[str]]:
    cleaned: list[str] = []
    resume_dir: Path | None = None
    status = False
    invalidated_roles: set[str] = set()
    index = 0
    while index < len(values):
        value = values[index]
        if value == "--status":
            status = True
            index += 1
            continue
        if value == "--resume":
            if index + 1 >= len(values):
                raise ManifestError("--resume requires a run directory")
            resume_dir = Path(values[index + 1]).expanduser().resolve()
            index += 2
            continue
        if value.startswith("--resume="):
            resume_dir = Path(value.split("=", 1)[1]).expanduser().resolve()
            index += 1
            continue
        if value == "--invalidate-role":
            if index + 1 >= len(values):
                raise ManifestError("--invalidate-role requires a role")
            invalidated_roles.add(values[index + 1].strip().casefold())
            index += 2
            continue
        if value.startswith("--invalidate-role="):
            invalidated_roles.add(value.split("=", 1)[1].strip().casefold())
            index += 1
            continue
        cleaned.append(value)
        index += 1
    unknown = sorted(invalidated_roles - set(MODEL_ROLES))
    if unknown:
        raise ManifestError("unknown --invalidate-role value(s): " + ", ".join(unknown))
    if (status or invalidated_roles) and resume_dir is None:
        raise ManifestError("--status and --invalidate-role require --resume <run-directory>")
    return cleaned, resume_dir, status, invalidated_roles


def _inject_resume_arguments(values: list[str], resume_dir: Path, manifest: dict[str, object]) -> list[str]:
    target = manifest.get("target", {})
    if not isinstance(target, dict):
        raise ManifestError("run manifest target is invalid")
    result = list(values)
    required = {
        "--repo": str(target["repo_path"]),
        "--github-repo": str(target["github_repo"]),
        "--issue": str(target["issue_number"]),
        "--out": str(resume_dir),
        "--mode": str(target["mode"]),
    }
    for flag, expected in required.items():
        supplied = _argument_value(result, flag)
        if supplied is not None and str(Path(supplied).expanduser().resolve() if flag in {"--repo", "--out"} else supplied) != str(Path(expected).expanduser().resolve() if flag in {"--repo", "--out"} else expected):
            raise ManifestError(f"resume {flag} does not match the manifest")
        if supplied is None:
            result.extend([flag, expected])
    provider_config = target.get("provider_config_path")
    if provider_config and _argument_value(result, "--provider-config") is None:
        result.extend(["--provider-config", str(provider_config)])
    options = target.get("options", {})
    if isinstance(options, dict):
        if _argument_value(result, "--max-fix-attempts") is None and options.get("max_fix_attempts") is not None:
            result.extend(["--max-fix-attempts", str(options["max_fix_attempts"])])
        flag_options = {
            "debug_artifacts": "--debug-artifacts",
            "skip_implementation": "--skip-implementation",
            "dry_run_implementation": "--dry-run-implementation",
            "baseline_verify": "--baseline-verify",
            "managed_labels": "--manage-labels",
        }
        for key, flag in flag_options.items():
            if options.get(key) and flag not in result:
                result.append(flag)
    return result


def _argument_value(values: list[str], flag: str) -> str | None:
    if flag in values:
        index = values.index(flag)
        return values[index + 1] if index + 1 < len(values) else ""
    prefix = flag + "="
    for value in values:
        if value.startswith(prefix):
            return value[len(prefix):]
    return None


def _build_role_snapshots(
    roles: dict[str, ModelConfig | None],
    policies: dict[str, str],
) -> dict[str, object]:
    snapshots: dict[str, object] = {}
    for role in MODEL_ROLES:
        config = roles.get(role)
        policy = role_policy_metadata(role, policies)
        if config is None:
            snapshots[role] = build_role_snapshot({}, {"enabled": False}, prompt_policy=policy)
            continue
        snapshots[role] = build_role_snapshot(
            model_config_to_dict(config),
            config.safe_metadata(),
            prompt_policy=policy,
        )
    return snapshots


def _update_resume_target_options(path: Path, args) -> None:
    manifest = load_manifest(path)
    target = manifest.get("target", {})
    if not isinstance(target, dict):
        raise ManifestError("run manifest target is invalid")
    target["provider_config_path"] = _provider_config_path(args.provider_config)
    target["options"] = {
        "max_fix_attempts": args.max_fix_attempts,
        "debug_artifacts": bool(args.debug_artifacts),
        "skip_implementation": bool(args.skip_implementation),
        "dry_run_implementation": bool(args.dry_run_implementation),
        "baseline_verify": bool(args.baseline_verify),
        "managed_labels": bool(args.manage_labels or args.next),
    }
    save_manifest(path, manifest)


def _provider_config_path(value: str | None) -> str:
    if not value:
        return ""
    return str(Path(value).expanduser().resolve())


def _validate_next_stage_provider(manifest: dict[str, object], roles: dict[str, ModelConfig | None]) -> None:
    role_for_stage = {
        "repository-read": "reader",
        "handoff-synthesized": "synthesizer",
        "plan-created": "planner",
        "implementation-generated": "implementer",
        "semantic-verified": "verifier",
    }
    role = role_for_stage.get(next_stage(manifest))
    if role is None:
        return
    config = roles.get(role)
    if config is None:
        raise RunnerError(f"resume requires a configured {role} provider for the next stage", 2)
    if config.api_key_env and not os.environ.get(config.api_key_env):
        raise RunnerError(
            f"resume requires environment variable {config.api_key_env} for the next {role} stage",
            2,
        )


def update_issue_labels(repo, github_repo, issue, *, add, remove, stream):
    for label in add:
        run_command(
            ["gh", "issue", "edit", str(issue), "--repo", github_repo, "--add-label", label],
            cwd=repo,
            stream=stream,
        )
    for label in remove:
        run_command(
            ["gh", "issue", "edit", str(issue), "--repo", github_repo, "--remove-label", label],
            cwd=repo,
            stream=stream,
        )


def select_issue(args, repo, stream):
    if not _ACTIVE_RESUMING.get():
        return _CORE_SELECT_ISSUE(args, repo, stream)
    manifest = _active_manifest_data()
    target = manifest["target"]
    selected = read_json(Path(args.out) / "selected-issue.json")  # noqa: F405
    return IssueSelection(  # noqa: F405
        number=int(target["issue_number"]),
        title=str(selected.get("title", "")) if isinstance(selected, dict) else "",
        url=str(selected.get("url", "")) if isinstance(selected, dict) else "",
        labels=list(selected.get("labels", [])) if isinstance(selected, dict) and isinstance(selected.get("labels"), list) else [],
        body=str(selected.get("body", "")) if isinstance(selected, dict) else "",
    )


def fetch_issue_text(github_repo, issue, repo, stream):
    if _ACTIVE_RESUMING.get():
        issue_path = Path(_active_args().out) / "issue.md"
        try:
            return issue_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise RunnerError(f"resume issue artifact is missing: {issue_path}", 2) from exc  # noqa: F405

    issue_text = _CORE_FETCH_ISSUE_TEXT(github_repo, issue, repo, stream)
    path = _active_manifest_path()
    if not path.is_file():
        base_sha = run_command(["git", "rev-parse", "HEAD"], cwd=repo, stream=stream).stdout.strip()  # noqa: F405
        branch = issue_branch_name(issue, issue_text)  # noqa: F405
        metadata = read_json(Path(_active_args().out) / "provider-metadata.json")  # noqa: F405
        prompt_policy = metadata.get("prompt_policy", {}) if isinstance(metadata, dict) else {}
        semantic = metadata.get("semantic_verification", {}) if isinstance(metadata, dict) else {}
        create_manifest(
            path,
            repo_path=repo,
            github_repo=github_repo,
            issue_number=issue,
            mode=_active_args().mode,
            base_sha=base_sha,
            branch=branch,
            role_snapshots=_ACTIVE_ROLE_SNAPSHOTS.get() or {},
            prompt_policy=prompt_policy if isinstance(prompt_policy, dict) else {},
            semantic_verification=semantic if isinstance(semantic, dict) else {},
        )
        _update_resume_target_options(path, _active_args())
    return issue_text


def ensure_clean_worktree(repo, stream):
    if not _ACTIVE_RESUMING.get():
        return _CORE_ENSURE_CLEAN_WORKTREE(repo, stream)
    _validate_resume_repository(repo, stream)


def ensure_issue_branch(repo, branch_name, stream):
    path = _active_manifest_path()
    if _ACTIVE_RESUMING.get():
        current = run_command(["git", "branch", "--show-current"], cwd=repo, stream=stream).stdout.strip()  # noqa: F405
        if current != branch_name:
            raise RunnerError(
                f"resume requires branch '{branch_name}', but current branch is '{current}'",
                2,
            )  # noqa: F405
    else:
        _CORE_ENSURE_ISSUE_BRANCH(repo, branch_name, stream)
    manifest = load_manifest(path)
    if not stage_completed(manifest, "issue-selected"):
        out_dir = Path(_active_args().out)
        complete_stage(
            path,
            "issue-selected",
            run_root=out_dir,
            artifacts=[out_dir / "selected-issue.json", out_dir / "issue.md"],
            inputs={
                "github_repo": _active_args().github_repo,
                "issue_number": _active_args().issue,
                "base_sha": manifest["target"]["base_sha"],
            },
            details={"branch": branch_name},
        )


def _validate_resume_repository(repo: Path, stream: TextIO) -> None:
    manifest = _active_manifest_data()
    target = manifest.get("target", {})
    if not isinstance(target, dict):
        raise RunnerError("resume manifest target is invalid", 2)  # noqa: F405
    if str(repo.resolve()) != str(Path(str(target["repo_path"])).resolve()):
        raise RunnerError("resume repository path does not match the manifest", 2)  # noqa: F405
    problems = validate_artifacts(manifest, Path(_active_args().out))
    if problems:
        raise RunnerError("resume artifact validation failed: " + "; ".join(problems), 2)  # noqa: F405
    current_branch = run_command(["git", "branch", "--show-current"], cwd=repo, stream=stream).stdout.strip()  # noqa: F405
    if current_branch != target["branch"]:
        raise RunnerError(
            f"resume branch mismatch: expected {target['branch']}, found {current_branch}",
            2,
        )  # noqa: F405
    base_sha = str(target["base_sha"])
    ancestry = run_command(
        ["git", "merge-base", "--is-ancestor", base_sha, "HEAD"],
        cwd=repo,
        stream=stream,
        check=False,
    )  # noqa: F405
    if ancestry.returncode != 0:
        raise RunnerError("resume refused because the branch no longer descends from the original base SHA", 2)  # noqa: F405
    current_head = run_command(["git", "rev-parse", "HEAD"], cwd=repo, stream=stream).stdout.strip()  # noqa: F405
    status_paths = sorted(changed_worktree_paths(repo, stream))  # noqa: F405
    if stage_completed(manifest, "pr-created"):
        expected_head = str(_stage_details(manifest, "pr-created").get("head_sha", ""))
        if expected_head and current_head != expected_head:
            raise RunnerError("resume refused because the PR branch head changed after PR creation", 2)  # noqa: F405
        if status_paths:
            raise RunnerError("resume refused because the working tree changed after PR creation", 2)  # noqa: F405
        return

    diff_text = run_command(["git", "diff", "--binary", "HEAD"], cwd=repo, stream=stream, check=False).stdout  # noqa: F405
    if stage_completed(manifest, "patch-applied"):
        patch_details = _stage_details(manifest, "patch-applied")
        expected_hash = str(patch_details.get("worktree_hash", ""))
        expected_paths = sorted(str(path) for path in patch_details.get("changed_paths", []) if str(path))
        actual_hash = hash_text(diff_text)
        if expected_hash != actual_hash or expected_paths != status_paths:
            if not diff_text and not status_paths and current_head != base_sha and _is_expected_autodev_commit(repo, stream, int(target["issue_number"])):
                return
            raise RunnerError("resume refused because the working tree changed after the recorded patch", 2)  # noqa: F405
    elif status_paths:
        raise RunnerError("resume refused because the working tree changed before patch application", 2)  # noqa: F405


def _is_expected_autodev_commit(repo: Path, stream: TextIO, issue: int) -> bool:
    subject = run_command(["git", "log", "-1", "--pretty=%s"], cwd=repo, stream=stream, check=False).stdout.strip()  # noqa: F405
    return subject == f"Implement issue {issue} with AutoDev runner"


def resolve_role_provider_configs(args) -> dict[str, ModelConfig | None]:
    file_config = load_provider_config(args.provider_config)
    defaults = {
        "reader": _core.default_ollama_command_config(_core.DEFAULT_READER_MODEL),
        "coder": _core.default_ollama_command_config(_core.DEFAULT_CODER_MODEL),
    }
    cli_values = {
        role: _core.provider_cli_values(args, role, file_config, defaults[role])
        for role in ("reader", "coder")
    }
    return resolve_role_configs(defaults=defaults, file_config=file_config, cli_values=cli_values)


def resolve_prompt_policy_configs(args) -> dict[str, str]:
    return resolve_prompt_policies(load_provider_config(args.provider_config))


def resolve_semantic_verification_settings(
    args,
    roles: dict[str, ModelConfig | None],
) -> SemanticSettings:
    return resolve_semantic_settings(
        load_provider_config(args.provider_config),
        verifier_configured=roles.get("verifier") is not None,
    )


def resolve_provider_configs(args):
    roles = _ACTIVE_ROLES.get() or resolve_role_provider_configs(args)
    reader = roles["reader"]
    implementer = roles["implementer"]
    assert reader is not None and implementer is not None
    return reader, implementer


def run_area_reader(repo, issue_text, reader_config, coder_config, out_dir, stream):
    roles = _roles_or_legacy(reader_config, coder_config)
    policies = _policies_or_default()
    if out_dir.exists() and not _ACTIVE_RESUMING.get():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    config_path = out_dir / "resolved-provider-roles.json"
    config_path.write_text(
        json.dumps(
            {
                "version": 2,
                "roles": {
                    role: model_config_to_dict(config)
                    for role, config in roles.items()
                    if config is not None
                },
                "prompt_policy": {
                    "enabled": any(mode != "off" for mode in policies.values()),
                    "roles": policies,
                },
                "semantic_verification": safe_semantic_metadata(_semantic_settings_or_disabled()),
            },
            indent=2,
            sort_keys=True,
        ) + "\n",
        encoding="utf-8",
    )
    argv = [
        "--repo", str(repo),
        "--reader-model", reader_config.model,
        "--coder-model", coder_config.model,
        "--provider-config", str(config_path),
        "--issue", issue_text,
        "--out", str(out_dir),
        "--resume-manifest", str(_active_manifest_path()),
    ]
    print("Running shared area-reader v2 planner", file=stream)
    try:
        exit_code = area_reader_runner.main(argv)  # noqa: F405
    except (ManifestError, ModelInvocationError) as exc:
        raise RunnerError(str(exc), 1) from exc  # noqa: F405
    if exit_code:
        raise RunnerError(f"area-reader v2 planner failed with exit code {exit_code}", exit_code)  # noqa: F405


def write_operational_outputs(issue_text, area_out, out_dir, keep_debug):
    source = area_out / "model-invocations.json"
    if source.is_file():
        shutil.copyfile(source, out_dir / "model-invocations.json")
        if keep_debug:
            try:
                records = json.loads(source.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                records = []
            if isinstance(records, list):
                for record in records:
                    if isinstance(record, dict):
                        write_compression_debug_artifact(out_dir, record)
    if _ACTIVE_MANIFEST.get() is None:
        _CORE_WRITE_OPERATIONAL_OUTPUTS(issue_text, area_out, out_dir, keep_debug)
        return
    if _ACTIVE_RESUMING.get():
        problems = validate_artifacts(load_manifest(_active_manifest_path()), out_dir)
        if problems:
            raise RunnerError("resume artifact validation failed after area-reader replay: " + "; ".join(problems), 2)  # noqa: F405
    # Area-reader checkpoint files are retained even without --debug-artifacts so reader/synthesis
    # model calls can be replayed safely during resume.
    _CORE_WRITE_OPERATIONAL_OUTPUTS(issue_text, area_out, out_dir, True)
    _refresh_operational_checkpoints(area_out, out_dir)
    _sync_manifest_invocations(out_dir)


def _refresh_operational_checkpoints(area_out: Path, out_dir: Path) -> None:
    manifest_file = _active_manifest_path()
    manifest = load_manifest(manifest_file)
    if stage_completed(manifest, "repository-read"):
        reader_artifacts = [
            out_dir / "routed-areas.json",
            out_dir / "detected-facts.json",
            out_dir / "recommended-command-groups.json",
            out_dir / "verification-command-groups.json",
            *sorted(area_out.glob("area-*/reader-brief.md")),
        ]
        complete_stage(
            manifest_file,
            "repository-read",
            run_root=out_dir,
            artifacts=reader_artifacts,
            inputs={
                "issue_sha256": _file_hash_or_empty(out_dir / "issue.md"),
                "reader_fingerprint": stage_role_fingerprint(manifest, "reader"),
            },
            details={"area_count": len(list(area_out.glob("area-*/reader-brief.md")))},
        )
        manifest = load_manifest(manifest_file)
    if stage_completed(manifest, "handoff-synthesized"):
        complete_stage(
            manifest_file,
            "handoff-synthesized",
            run_root=out_dir,
            artifacts=[out_dir / "synthesized-handoff.md", area_out / "synthesis-prompt.txt", area_out / "synthesis-brief.md"],
            inputs={
                "repository_read_output": _stage_output_hash(manifest, "repository-read"),
                "synthesizer_fingerprint": stage_role_fingerprint(manifest, "synthesizer"),
            },
        )
        manifest = load_manifest(manifest_file)
    if stage_completed(manifest, "plan-created"):
        complete_stage(
            manifest_file,
            "plan-created",
            run_root=out_dir,
            artifacts=[out_dir / "coder-plan.md", area_out / "coder-prompt.txt", area_out / "coder-plan.md"],
            inputs={
                "handoff_output": _stage_output_hash(manifest, "handoff-synthesized"),
                "planner_fingerprint": stage_role_fingerprint(manifest, "planner"),
            },
        )


def run_implementation_loop(
    *, repo, out_dir, issue_text, branch_name,
    coder_provider=None, coder_config=None,
    implementer_provider=None, implementer_config=None,
    fixer_provider=None, fixer_config=None,
    max_fix_attempts, dry_run, stream,
):
    if _ACTIVE_MANIFEST.get() is None:
        legacy_provider = coder_provider or implementer_provider
        legacy_config = coder_config or implementer_config
        if legacy_provider is None or legacy_config is None:
            raise RunnerError("coder provider and configuration are required")  # noqa: F405
        return _CORE_RUN_IMPLEMENTATION_LOOP(
            repo=repo,
            out_dir=out_dir,
            issue_text=issue_text,
            branch_name=branch_name,
            coder_provider=legacy_provider,
            coder_config=legacy_config,
            max_fix_attempts=max_fix_attempts,
            dry_run=dry_run,
            stream=stream,
        )

    roles = _roles_or_legacy(None, coder_config)
    policies = _policies_or_default()
    implementer_config = implementer_config or roles["implementer"] or coder_config
    fixer_config = fixer_config or roles["fixer"] or implementer_config
    factory = _ACTIVE_FACTORY.get() or create_provider
    manifest_file = _active_manifest_path()
    manifest = load_manifest(manifest_file)

    if stage_completed(manifest, "semantic-verified") and stage_completed(manifest, "deterministic-verified"):
        return _resumed_verification(out_dir)

    patch: Path | None
    if stage_completed(manifest, "implementation-generated"):
        details = _stage_details(manifest, "implementation-generated")
        patch_value = str(details.get("patch_path", ""))
        patch = out_dir / patch_value if patch_value else None
    else:
        implementer_provider = implementer_provider or coder_provider or factory(implementer_config)
        prompt = build_implementation_prompt(  # noqa: F405
            issue_text=issue_text,
            synthesized_handoff=read_optional_text(out_dir / "synthesized-handoff.md"),  # noqa: F405
            coder_plan=read_optional_text(out_dir / "coder-plan.md"),  # noqa: F405
            recommended_command_groups=read_optional_text(out_dir / "recommended-command-groups.json"),  # noqa: F405
            constraints=read_optional_text(PROMPT_TEMPLATE_DIR / "implementer.md"),  # noqa: F405
            branch_name=branch_name,
        )
        prompt = compose_prompt("implementer", prompt, policies["implementer"])
        write_text(out_dir / "implementation-prompt.md", prompt)  # noqa: F405
        response = call_coder(implementer_provider, implementer_config, prompt, out_dir, 0, role="implementer")
        patch = process_model_response(response, out_dir, 0)  # noqa: F405
        response_path = out_dir / "model-responses" / "attempt-0.txt"
        patch_artifact = patch or (out_dir / "model-patches" / "attempt-0.txt")
        complete_stage(
            manifest_file,
            "implementation-generated",
            run_root=out_dir,
            artifacts=[out_dir / "implementation-prompt.md", response_path, patch_artifact],
            inputs={
                "plan_sha256": _file_hash_or_empty(out_dir / "coder-plan.md"),
                "implementer_fingerprint": stage_role_fingerprint(load_manifest(manifest_file), "implementer"),
            },
            details={
                "patch_path": patch.relative_to(out_dir).as_posix() if patch is not None else "",
                "patch_hash": hash_file(patch) if patch is not None else "",
                "no_changes": patch is None,
            },
        )
        manifest = load_manifest(manifest_file)

    if dry_run:
        return VerificationResult(0, 0, "dry-run", "Dry-run implementation did not apply patch.", "", out_dir / "verification" / "attempt-0.md")  # noqa: F405

    if not stage_completed(manifest, "patch-applied"):
        if patch is not None:
            apply_patch_file(repo, patch, stream)
        _checkpoint_patch_applied(out_dir, repo, patch, stream, no_changes=patch is None)
        manifest = load_manifest(manifest_file)

    pending_repair = _pending_repair_patch(out_dir, manifest, kind="deterministic")
    if pending_repair is not None:
        repair_patch, repair_attempt = pending_repair
        if not _patch_is_recorded_as_applied(manifest, repair_patch):
            apply_patch_file(repo, repair_patch, stream)
            _checkpoint_patch_applied(out_dir, repo, repair_patch, stream, attempt=repair_attempt)
            manifest = load_manifest(manifest_file)

    if stage_completed(manifest, "deterministic-verified"):
        verification = _resumed_verification(out_dir)
    elif patch is None and not _stage_details(manifest, "patch-applied").get("last_patch_hash"):
        verification = VerificationResult(0, 0, "no-change", "NO_CHANGES_REQUIRED", "", out_dir / "verification" / "attempt-0.md")  # noqa: F405
        write_verification_attempt(verification)  # noqa: F405
        write_verification_result(out_dir, verification)  # noqa: F405
        _checkpoint_deterministic(out_dir, repo, verification, stream, no_changes=True)
    else:
        attempt = int(_stage_details(manifest, "repair-generated").get("attempt", 0)) if pending_repair else 0
        verification = run_recommended_verification(out_dir, repo, attempt, stream)  # noqa: F405
        write_verification_result(out_dir, verification)  # noqa: F405
        if verification.passed:
            _checkpoint_deterministic(out_dir, repo, verification, stream)
        else:
            record_stage_state(
                manifest_file,
                "deterministic-verified",
                status="failed",
                details={"attempt": attempt, "returncode": verification.returncode, "command_group": verification.command_group},
            )

    attempt = max(1, _next_fix_attempt(out_dir))
    while not verification.passed and attempt <= max_fix_attempts:
        if fixer_provider is None:
            fixer_provider = factory(fixer_config)
        fix_prompt = build_fix_prompt(  # noqa: F405
            issue_text=issue_text,
            synthesized_handoff=read_optional_text(out_dir / "synthesized-handoff.md"),  # noqa: F405
            coder_plan=read_optional_text(out_dir / "coder-plan.md"),  # noqa: F405
            previous_response=read_optional_text(out_dir / "model-responses" / f"attempt-{attempt - 1}.txt"),  # noqa: F405
            current_diff=current_diff(repo, stream),  # noqa: F405
            verification=verification,
        )
        fix_prompt = compose_prompt("fixer", fix_prompt, policies["fixer"])
        write_text(out_dir / "fix-prompt.md", fix_prompt)  # noqa: F405
        response = call_coder(fixer_provider, fixer_config, fix_prompt, out_dir, attempt, role="fixer")
        repair_patch = process_model_response(response, out_dir, attempt)  # noqa: F405
        if repair_patch is None:
            record_failure(
                manifest_file,
                classification="repair_no_changes",
                reason="fixer returned NO_CHANGES_REQUIRED while deterministic verification was failing",
                stage="repair-generated",
            )
            break
        complete_stage(
            manifest_file,
            "repair-generated",
            run_root=out_dir,
            artifacts=[out_dir / "fix-prompt.md", out_dir / "model-responses" / f"attempt-{attempt}.txt", repair_patch],
            inputs={
                "verification_sha256": _file_hash_or_empty(verification.summary_path),
                "fixer_fingerprint": stage_role_fingerprint(load_manifest(manifest_file), "fixer"),
            },
            details={
                "kind": "deterministic",
                "attempt": attempt,
                "patch_path": repair_patch.relative_to(out_dir).as_posix(),
                "patch_hash": hash_file(repair_patch),
            },
        )
        apply_patch_file(repo, repair_patch, stream)
        _checkpoint_patch_applied(out_dir, repo, repair_patch, stream, attempt=attempt)
        verification = run_recommended_verification(out_dir, repo, attempt, stream)  # noqa: F405
        write_verification_result(out_dir, verification)  # noqa: F405
        if verification.passed:
            _checkpoint_deterministic(out_dir, repo, verification, stream)
        else:
            record_stage_state(
                manifest_file,
                "deterministic-verified",
                status="failed",
                details={"attempt": attempt, "returncode": verification.returncode, "command_group": verification.command_group},
            )
        attempt += 1

    if not verification.passed:
        record_failure(
            manifest_file,
            classification="deterministic_verification_failed",
            reason="deterministic verification failed after configured repair attempts",
            stage="deterministic-verified",
        )
        return verification
    return run_semantic_verification_gate(
        repo=repo,
        out_dir=out_dir,
        issue_text=issue_text,
        verification=verification,
        roles=roles,
        fixer_provider=fixer_provider,
        fixer_config=fixer_config,
        factory=factory,
        stream=stream,
    )


def apply_patch_file(repo: Path, patch_path: Path, stream: TextIO) -> bool:
    reverse = run_command(["git", "apply", "--check", "--reverse", str(patch_path)], cwd=repo, stream=stream, check=False)  # noqa: F405
    if reverse.returncode == 0:
        return False
    result = run_command(["git", "apply", "--index", str(patch_path)], cwd=repo, stream=stream, check=False)  # noqa: F405
    if result.returncode == 0:
        return True
    fallback = run_command(["git", "apply", str(patch_path)], cwd=repo, stream=stream, check=False)  # noqa: F405
    if fallback.returncode != 0:
        raise RunnerError("patch application failed\n" + format_command_failure(fallback))  # noqa: F405
    return True


def _checkpoint_patch_applied(
    out_dir: Path,
    repo: Path,
    patch: Path | None,
    stream: TextIO,
    *,
    attempt: int = 0,
    no_changes: bool = False,
) -> None:
    diff_text = run_command(["git", "diff", "--binary", "HEAD"], cwd=repo, stream=stream, check=False).stdout  # noqa: F405
    changed_paths = changed_worktree_paths(repo, stream)  # noqa: F405
    artifacts = [patch] if patch is not None else []
    complete_stage(
        _active_manifest_path(),
        "patch-applied",
        run_root=out_dir,
        artifacts=artifacts,
        inputs={"patch_hash": hash_file(patch) if patch is not None else "no-changes"},
        details={
            "attempt": attempt,
            "no_changes": no_changes,
            "last_patch_hash": hash_file(patch) if patch is not None else "",
            "worktree_hash": hash_text(diff_text),
            "changed_paths": changed_paths,
        },
    )


def _checkpoint_deterministic(
    out_dir: Path,
    repo: Path,
    verification,
    stream: TextIO,
    *,
    no_changes: bool = False,
) -> None:
    diff_text = run_command(["git", "diff", "--binary", "HEAD"], cwd=repo, stream=stream, check=False).stdout  # noqa: F405
    complete_stage(
        _active_manifest_path(),
        "deterministic-verified",
        run_root=out_dir,
        artifacts=[verification.summary_path, out_dir / "verification-result-summary.md"],
        inputs={
            "patch_output": _stage_output_hash(load_manifest(_active_manifest_path()), "patch-applied"),
            "verification_groups_sha256": _file_hash_or_empty(out_dir / "recommended-command-groups.json"),
        },
        details={
            "attempt": verification.attempt,
            "returncode": verification.returncode,
            "command_group": verification.command_group,
            "worktree_hash": hash_text(diff_text),
            "no_changes": no_changes,
        },
    )


def _pending_repair_patch(out_dir: Path, manifest: dict[str, object], *, kind: str) -> tuple[Path, int] | None:
    if not stage_completed(manifest, "repair-generated"):
        return None
    details = _stage_details(manifest, "repair-generated")
    if details.get("kind") != kind:
        return None
    relative = str(details.get("patch_path", ""))
    if not relative:
        return None
    return out_dir / relative, int(details.get("attempt", 0))


def _patch_is_recorded_as_applied(manifest: dict[str, object], patch: Path) -> bool:
    if not stage_completed(manifest, "patch-applied"):
        return False
    return str(_stage_details(manifest, "patch-applied").get("last_patch_hash", "")) == hash_file(patch)


def _next_fix_attempt(out_dir: Path) -> int:
    attempts = [0]
    for path in (out_dir / "model-patches").glob("attempt-*.*"):
        match = re.match(r"attempt-(\d+)", path.name)
        if match:
            attempts.append(int(match.group(1)))
    return max(attempts) + 1


def _resumed_verification(out_dir: Path) -> VerificationResult:
    return VerificationResult(  # noqa: F405
        0,
        0,
        "resumed",
        read_optional_text(out_dir / "verification-result-summary.md"),  # noqa: F405
        "",
        out_dir / "verification-result-summary.md",
    )


def run_semantic_verification_gate(
    *,
    repo: Path,
    out_dir: Path,
    issue_text: str,
    verification,
    roles: dict[str, ModelConfig | None],
    fixer_provider,
    fixer_config,
    factory,
    stream,
):
    settings = _semantic_settings_or_disabled()
    manifest_file = _active_manifest_path()
    manifest = load_manifest(manifest_file)
    if stage_completed(manifest, "semantic-verified"):
        return verification
    if not settings.enabled:
        complete_stage(
            manifest_file,
            "semantic-verified",
            run_root=out_dir,
            inputs={"deterministic_output": _stage_output_hash(manifest, "deterministic-verified")},
            details={"enabled": False, "verdict": "not-configured"},
        )
        return verification

    resumed_repair = _pending_repair_patch(out_dir, manifest, kind="semantic")
    if resumed_repair is not None:
        repair_patch, repair_attempt = resumed_repair
        if not _patch_is_recorded_as_applied(manifest, repair_patch):
            _clear_completed_stages(manifest_file, ["deterministic-verified", "semantic-verified", "pr-created"], "semantic repair patch")
            apply_patch_file(repo, repair_patch, stream)
            _checkpoint_patch_applied(out_dir, repo, repair_patch, stream, attempt=repair_attempt)
            manifest = load_manifest(manifest_file)
        elif not _deterministic_matches_current_patch(manifest):
            _clear_completed_stages(manifest_file, ["deterministic-verified", "semantic-verified", "pr-created"], "semantic repair requires re-verification")
            manifest = load_manifest(manifest_file)
        deterministic_attempt = int(getattr(verification, "attempt", 0)) + 1
        if not stage_completed(manifest, "deterministic-verified"):
            verification = run_recommended_verification(out_dir, repo, deterministic_attempt, stream)  # noqa: F405
            write_verification_result(out_dir, verification)  # noqa: F405
            if not verification.passed:
                record_failure(
                    manifest_file,
                    classification="deterministic_verification_failed",
                    reason="deterministic verification failed after resumed semantic repair",
                    stage="deterministic-verified",
                )
                return verification
            _checkpoint_deterministic(out_dir, repo, verification, stream)
        return _run_final_semantic_attempt(repo, out_dir, issue_text, verification, roles, settings, factory)

    verifier_config = roles.get("verifier")
    if verifier_config is None:
        raise RunnerError("semantic verification is enabled but verifier role is unavailable")  # noqa: F405
    verifier_provider = factory(verifier_config)
    result = _invoke_semantic_attempt(
        repo=repo,
        out_dir=out_dir,
        issue_text=issue_text,
        verifier_provider=verifier_provider,
        verifier_config=verifier_config,
        semantic_attempt=0,
        call_attempt=0,
        settings=settings,
    )
    semantic_path = write_semantic_result(out_dir, 0, result)

    if result["verdict"] == "pass":
        final_path = write_final_verdict(out_dir, result)
        _checkpoint_semantic(out_dir, result, [semantic_path, final_path])
        return verification
    if result["verdict"] == "blocked":
        write_final_verdict(out_dir, result)
        record_stage_state(manifest_file, "semantic-verified", status="blocked", details={"verdict": "blocked"})
        record_failure(
            manifest_file,
            classification="semantic_blocked",
            reason=str(result.get("repair_brief", "semantic verifier blocked the run")),
            stage="semantic-verified",
        )
        raise RunnerError("semantic verification blocked the run: " + str(result.get("repair_brief", "")))  # noqa: F405
    if settings.max_repair_attempts < 1:
        write_final_verdict(out_dir, result)
        record_failure(
            manifest_file,
            classification="semantic_repair_disabled",
            reason="semantic verification requested repair but semantic repair is disabled",
            stage="semantic-verified",
        )
        raise RunnerError("semantic verification requested repair but semantic repair is disabled")  # noqa: F405

    repair_brief_path = out_dir / "verification" / "repair-brief.md"
    write_text(repair_brief_path, str(result.get("repair_brief", "")).strip() + "\n")  # noqa: F405
    changed_files = collect_changed_files(repo)
    repair_prompt = build_semantic_repair_prompt(
        issue_text=issue_text,
        plan=read_optional_text(out_dir / "coder-plan.md"),  # noqa: F405
        semantic_result=result,
        changed_files=changed_files,
        diff=collect_current_diff(repo, changed_files),
        template=read_optional_text(PROMPT_TEMPLATE_DIR / "verification-repair.md"),  # noqa: F405
    )
    semantic_repair_prompt = out_dir / "verification" / "semantic-repair-prompt.md"
    write_text(semantic_repair_prompt, repair_prompt)  # noqa: F405
    if fixer_provider is None:
        fixer_provider = factory(fixer_config)
    repair_response = call_coder(
        fixer_provider,
        fixer_config,
        repair_prompt,
        out_dir,
        0,
        role="fixer",
        response_name="semantic-fixer-attempt-0.txt",
    )
    if parse_no_changes_required(repair_response) is not None:  # noqa: F405
        write_final_verdict(out_dir, result)
        raise RunnerError("semantic fixer returned NO_CHANGES_REQUIRED without a final semantic pass")  # noqa: F405
    patch_text = extract_unified_diff(repair_response)  # noqa: F405
    if not patch_text:
        raise RunnerError("semantic fixer did not return a valid patch")  # noqa: F405
    patch_path = out_dir / "verification" / "semantic-repair-attempt-0.patch"
    write_text(patch_path, patch_text)  # noqa: F405
    complete_stage(
        manifest_file,
        "repair-generated",
        run_root=out_dir,
        artifacts=[repair_brief_path, semantic_repair_prompt, out_dir / "model-responses" / "semantic-fixer-attempt-0.txt", patch_path],
        inputs={
            "semantic_result_sha256": _file_hash_or_empty(semantic_path),
            "fixer_fingerprint": stage_role_fingerprint(load_manifest(manifest_file), "fixer"),
        },
        details={
            "kind": "semantic",
            "attempt": 0,
            "patch_path": patch_path.relative_to(out_dir).as_posix(),
            "patch_hash": hash_file(patch_path),
        },
    )
    _clear_completed_stages(manifest_file, ["deterministic-verified", "semantic-verified", "pr-created"], "semantic repair patch")
    apply_patch_file(repo, patch_path, stream)  # noqa: F405
    _checkpoint_patch_applied(out_dir, repo, patch_path, stream)

    deterministic_attempt = int(getattr(verification, "attempt", 0)) + 1
    verification = run_recommended_verification(out_dir, repo, deterministic_attempt, stream)  # noqa: F405
    write_verification_result(out_dir, verification)  # noqa: F405
    if not verification.passed:
        blocked = dict(result)
        blocked["verdict"] = "blocked"
        blocked["repair_brief"] = "Deterministic verification failed after semantic repair."
        write_final_verdict(out_dir, blocked)
        record_failure(
            manifest_file,
            classification="deterministic_verification_failed",
            reason="deterministic verification failed after semantic repair",
            stage="deterministic-verified",
        )
        return verification
    _checkpoint_deterministic(out_dir, repo, verification, stream)
    return _run_final_semantic_attempt(repo, out_dir, issue_text, verification, roles, settings, factory)


def _deterministic_matches_current_patch(manifest: dict[str, object]) -> bool:
    if not stage_completed(manifest, "deterministic-verified") or not stage_completed(manifest, "patch-applied"):
        return False
    deterministic_hash = str(_stage_details(manifest, "deterministic-verified").get("worktree_hash", ""))
    patch_hash = str(_stage_details(manifest, "patch-applied").get("worktree_hash", ""))
    return bool(deterministic_hash and deterministic_hash == patch_hash)


def _clear_completed_stages(path: Path, stages_to_clear: list[str], reason: str) -> None:
    manifest = load_manifest(path)
    stages = manifest.get("stages", {})
    completed = manifest.get("completed_stages", [])
    invalidations = manifest.setdefault("invalidations", [])
    if not isinstance(stages, dict) or not isinstance(completed, list) or not isinstance(invalidations, list):
        raise ManifestError("run manifest stage state is invalid")
    for stage in stages_to_clear:
        record = stages.pop(stage, None)
        if stage in completed:
            completed.remove(stage)
        if record is not None:
            invalidations.append(
                {
                    "stage": stage,
                    "role": "workflow",
                    "reason": reason,
                    "invalidated_at": datetime.now(timezone.utc).isoformat(),
                    "previous_output_hash": record.get("output_hash", "") if isinstance(record, dict) else "",
                }
            )
    manifest["current_stage"] = completed[-1] if completed else ""
    manifest["failure"] = {}
    save_manifest(path, manifest)


def _run_final_semantic_attempt(repo, out_dir, issue_text, verification, roles, settings, factory):
    verifier_config = roles.get("verifier")
    if verifier_config is None:
        raise RunnerError("semantic verification is enabled but verifier role is unavailable")  # noqa: F405
    verifier_provider = factory(verifier_config)
    final_result = _invoke_semantic_attempt(
        repo=repo,
        out_dir=out_dir,
        issue_text=issue_text,
        verifier_provider=verifier_provider,
        verifier_config=verifier_config,
        semantic_attempt=1,
        call_attempt=settings.max_schema_retries + 1,
        settings=settings,
    )
    semantic_path = write_semantic_result(out_dir, 1, final_result)
    final_path = write_final_verdict(out_dir, final_result)
    if final_result["verdict"] != "pass":
        record_failure(
            _active_manifest_path(),
            classification="semantic_not_passed",
            reason="semantic verification did not pass after the targeted repair",
            stage="semantic-verified",
        )
        raise RunnerError("semantic verification did not pass after the targeted repair")  # noqa: F405
    _checkpoint_semantic(out_dir, final_result, [semantic_path, final_path])
    return verification


def _checkpoint_semantic(out_dir: Path, result: dict[str, object], artifacts: list[Path]) -> None:
    manifest = load_manifest(_active_manifest_path())
    complete_stage(
        _active_manifest_path(),
        "semantic-verified",
        run_root=out_dir,
        artifacts=artifacts,
        inputs={
            "deterministic_output": _stage_output_hash(manifest, "deterministic-verified"),
            "verifier_fingerprint": stage_role_fingerprint(manifest, "verifier"),
        },
        details={"enabled": True, "verdict": result.get("verdict", "")},
    )


def _invoke_semantic_attempt(
    *,
    repo: Path,
    out_dir: Path,
    issue_text: str,
    verifier_provider,
    verifier_config,
    semantic_attempt: int,
    call_attempt: int,
    settings: SemanticSettings,
) -> dict[str, object]:
    changed_files = collect_changed_files(repo)
    prompt = build_semantic_prompt(
        issue_text=issue_text,
        synthesized_handoff=read_optional_text(out_dir / "synthesized-handoff.md"),  # noqa: F405
        plan=read_optional_text(out_dir / "coder-plan.md"),  # noqa: F405
        changed_files=changed_files,
        diff=collect_current_diff(repo, changed_files),
        deterministic_evidence=(
            read_optional_text(out_dir / "verification-result-summary.md")  # noqa: F405
            + "\n\n"
            + read_optional_text(out_dir / "recommended-command-groups.json")  # noqa: F405
        ),
        uncertainty_notes=read_optional_text(out_dir / "verification-notes.md"),  # noqa: F405
        template=read_optional_text(PROMPT_TEMPLATE_DIR / "verifier.md"),  # noqa: F405
    )
    write_text(out_dir / "verification" / f"semantic-prompt-{semantic_attempt}.md", prompt)  # noqa: F405
    current_prompt = prompt
    for schema_attempt in range(settings.max_schema_retries + 1):
        response = call_coder(
            verifier_provider,
            verifier_config,
            current_prompt,
            out_dir,
            call_attempt + schema_attempt,
            role="verifier",
            response_name=f"semantic-verifier-{semantic_attempt}-schema-{schema_attempt}.txt",
        )
        try:
            return parse_semantic_output(response)
        except SemanticVerifierError as exc:
            if schema_attempt >= settings.max_schema_retries:
                record_failure(
                    _active_manifest_path(),
                    classification="malformed_semantic_output",
                    reason=str(exc),
                    stage="semantic-verified",
                )
                raise RunnerError(f"semantic verifier output remained malformed: {exc}") from exc  # noqa: F405
            current_prompt = build_schema_repair_prompt(prompt, response, str(exc))
    raise RunnerError("semantic verifier did not return a valid verdict")  # noqa: F405


def call_coder(provider, config, prompt, out_dir, attempt, *, role="implementer", response_name=None):
    metadata_path = out_dir / "model-invocations.json"
    policies = _policies_or_default()
    prompt = compose_prompt(role, prompt, policies[role])
    policy_metadata = role_policy_metadata(role, policies)
    try:
        response, record = invoke_model(provider, config, prompt, role=role, attempt=attempt)
    except ModelInvocationError as exc:
        exc.record.update(policy_metadata)
        append_invocation_metadata(metadata_path, exc.record)
        if _ACTIVE_MANIFEST.get() is not None:
            _sync_manifest_invocations(out_dir)
            record_failure(
                _active_manifest_path(),
                classification=str(exc.record.get("failure_classification", "provider_error")),
                reason=f"{role} provider invocation failed",
                stage=_stage_for_model_role(role),
            )
        raise
    record.update(policy_metadata)
    append_invocation_metadata(metadata_path, record)
    if _ACTIVE_MANIFEST.get() is not None:
        _sync_manifest_invocations(out_dir)
    if _ACTIVE_DEBUG_ARTIFACTS.get():
        write_compression_debug_artifact(out_dir, record)
    name = response_name or f"attempt-{attempt}.txt"
    write_text(out_dir / "model-responses" / name, response)  # noqa: F405
    return response


def _stage_for_model_role(role: str) -> str:
    return {
        "reader": "repository-read",
        "synthesizer": "handoff-synthesized",
        "planner": "plan-created",
        "implementer": "implementation-generated",
        "fixer": "repair-generated",
        "verifier": "semantic-verified",
    }.get(role, "")


def write_compression_debug_artifact(out_dir: Path, record: dict[str, object]) -> None:
    compression = record.get("compression")
    if not isinstance(compression, dict):
        return
    role = str(record.get("role", "unknown"))
    attempt = record.get("attempt", 0)
    payload = {
        "role": role,
        "attempt": attempt,
        "transport": record.get("transport", record.get("provider", "")),
        "model": record.get("model", ""),
        **compression,
    }
    write_json(out_dir / "compression" / f"{role}-attempt-{attempt}.json", payload)  # noqa: F405


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


def write_provider_metadata(out_dir, reader_config, coder_config):
    roles = _roles_or_legacy(reader_config, coder_config)
    snapshots = _ACTIVE_ROLE_SNAPSHOTS.get() or _build_role_snapshots(roles, _policies_or_default())
    write_json(  # noqa: F405
        out_dir / "provider-metadata.json",
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "reader": reader_config.safe_metadata(),
            "coder": coder_config.safe_metadata(),
            "roles": safe_role_metadata(roles),
            "prompt_policy": safe_prompt_policy_metadata(_policies_or_default()),
            "semantic_verification": safe_semantic_metadata(_semantic_settings_or_disabled()),
            "resume_role_snapshots": snapshots,
        },
    )


def _sync_manifest_invocations(out_dir: Path) -> None:
    path = _ACTIVE_MANIFEST.get()
    if path is not None and path.is_file():
        sync_invocations(path, out_dir / "model-invocations.json")


def _active_args():
    args = _ACTIVE_ARGS.get()
    if args is None:
        raise RunnerError("run context is unavailable", 2)  # noqa: F405
    return args


def _active_manifest_path() -> Path:
    path = _ACTIVE_MANIFEST.get()
    if path is None:
        args = _active_args()
        path = Path(args.out).expanduser().resolve() / MANIFEST_NAME
    return path


def _active_manifest_data() -> dict[str, object]:
    return load_manifest(_active_manifest_path())


def _stage_details(manifest: dict[str, object], stage: str) -> dict[str, object]:
    stages = manifest.get("stages", {})
    record = stages.get(stage, {}) if isinstance(stages, dict) else {}
    details = record.get("details", {}) if isinstance(record, dict) else {}
    return dict(details) if isinstance(details, dict) else {}


def _stage_output_hash(manifest: dict[str, object], stage: str) -> str:
    stages = manifest.get("stages", {})
    record = stages.get(stage, {}) if isinstance(stages, dict) else {}
    return str(record.get("output_hash", "")) if isinstance(record, dict) else ""


def _file_hash_or_empty(path: Path) -> str:
    return hash_file(path) if path.is_file() else ""


def _roles_or_legacy(reader_config, coder_config):
    roles = _ACTIVE_ROLES.get()
    if roles is not None:
        return roles
    return {
        "reader": reader_config,
        "synthesizer": reader_config,
        "planner": coder_config,
        "implementer": coder_config,
        "fixer": coder_config,
        "verifier": None,
    }


def _policies_or_default() -> dict[str, str]:
    return _ACTIVE_POLICIES.get() or resolve_prompt_policies({})


def _semantic_settings_or_disabled() -> SemanticSettings:
    return _ACTIVE_SEMANTIC.get() or SemanticSettings(False)


if __name__ == "__main__":
    raise SystemExit(main())
