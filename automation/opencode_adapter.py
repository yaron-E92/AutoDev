from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from area_reader_v2 import runner_core as area_reader_core
from automation import run_real_issue_core as run_core
from automation.model_output_sanitizer import sanitize_model_output
from automation.model_providers import ProviderError, load_provider_config
from automation.prompt_policies import compose_prompt, resolve_prompt_policies
from automation.prompt_runner import PromptRunnerError, handle_planner_output
from automation.semantic_verifier import (
    SemanticVerifierError,
    build_semantic_prompt,
    collect_changed_files,
    collect_current_diff,
    collect_deterministic_evidence,
    extract_acceptance_criteria,
    parse_semantic_output,
    prepare_semantic_repair_prompt,
    render_template,
    write_final_verdict,
    write_semantic_result,
)


AUTODEV_ROOT = Path(__file__).resolve().parents[1]
CURRENT_DIR = Path(".codex-run") / "current"
COMMAND_FILES = (
    "autodev-issue-to-pr.md",
    "autodev-read.md",
    "autodev-plan.md",
    "autodev-implement.md",
    "autodev-fix.md",
    "autodev-verify.md",
)
AGENT_FILES = (
    "autodev-coordinator.md",
    "autodev-reader.md",
    "autodev-synthesizer.md",
    "autodev-planner.md",
    "autodev-implementer.md",
    "autodev-fixer.md",
    "autodev-verifier.md",
)
COORDINATOR_STAGES = (
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
MAX_HANDOFF_CHARS = 30_000
MAX_READER_BUNDLE_CHARS = 24_000
DEFAULT_MAX_REPAIR_ATTEMPTS = 3
DEFAULT_MAX_SEMANTIC_REPAIR_ATTEMPTS = 1


class OpenCodeAdapterError(RuntimeError):
    pass


def install_assets(
    target_repo: Path,
    autodev_root: Path = AUTODEV_ROOT,
    *,
    python_command: str = "python",
) -> list[Path]:
    target_repo = target_repo.expanduser().resolve()
    autodev_root = autodev_root.expanduser().resolve()
    if not target_repo.is_dir():
        raise OpenCodeAdapterError(f"target repository is not a directory: {target_repo}")

    source = autodev_root / "integrations" / "opencode"
    target = target_repo / ".opencode"
    installed: list[Path] = []
    for directory, names in (("commands", COMMAND_FILES), ("agents", AGENT_FILES)):
        destination = target / directory
        destination.mkdir(parents=True, exist_ok=True)
        for name in names:
            source_file = source / directory / name
            if not source_file.is_file():
                raise OpenCodeAdapterError(f"missing canonical OpenCode asset: {source_file}")
            target_file = destination / name
            shutil.copyfile(source_file, target_file)
            installed.append(target_file)

    wrapper_source = source / "autodev.ps1"
    wrapper_target = target / "autodev.ps1"
    if not wrapper_source.is_file():
        raise OpenCodeAdapterError(f"missing canonical OpenCode bridge wrapper: {wrapper_source}")
    target.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(wrapper_source, wrapper_target)
    installed.append(wrapper_target)

    config_path = target / "autodev.json"
    config_path.write_text(
        json.dumps(
            {
                "version": 1,
                "autodev_root": str(autodev_root),
                "python": python_command,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    installed.append(config_path)
    return installed


def issue_number_from_arguments(arguments: str) -> int:
    match = re.search(r"(?<!\d)#?(\d+)(?!\d)", arguments or "")
    return int(match.group(1)) if match else 0


def ensure_current_issue(
    repo: Path,
    autodev_root: Path,
    arguments: str,
    *,
    runner=subprocess.run,
) -> Path:
    current = repo / CURRENT_DIR
    requested_issue = issue_number_from_arguments(arguments)
    state = _read_json(current / "state.json")
    current_issue = int(state.get("IssueNumber", 0) or 0) if isinstance(state, dict) else 0
    if current.is_dir() and (requested_issue == 0 or requested_issue == current_issue):
        return current
    if requested_issue == 0:
        raise OpenCodeAdapterError(
            "no prepared AutoDev issue is available; pass an issue number to the OpenCode command"
        )

    workflow = autodev_root / "windows" / "scripts" / "issue-to-pr-cycle.ps1"
    command = [
        "pwsh",
        "-NoProfile",
        "-File",
        str(workflow),
        "-Mode",
        "Prepare",
        "-WorkingDirectory",
        str(repo),
        "-Issue",
        str(requested_issue),
        "-ForceCurrent",
        "-PromptDir",
        str(autodev_root / "promptTemplates"),
        "-ProfilesPath",
        str(autodev_root / "codex-profiles.json"),
    ]
    env = _workflow_environment()
    completed = runner(command, cwd=repo, env=env, text=True, capture_output=True, check=False)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "Prepare failed.").strip()
        raise OpenCodeAdapterError(f"AutoDev Prepare failed: {detail}")
    state = _read_json(current / "state.json")
    if not isinstance(state, dict) or int(state.get("IssueNumber", 0) or 0) != requested_issue:
        raise OpenCodeAdapterError("AutoDev Prepare did not create the requested current issue state")
    return current


def prepare_role(
    role: str,
    repo: Path,
    arguments: str,
    *,
    autodev_root: Path = AUTODEV_ROOT,
) -> Path:
    repo = repo.expanduser().resolve()
    autodev_root = autodev_root.expanduser().resolve()
    current = ensure_current_issue(repo, autodev_root, arguments)
    state = _read_state(current)
    issue_text = _read_text(current / "issue.md") or str(state.get("IssueText", ""))
    policies = _resolved_policies(repo, state)

    if role == "reader":
        prompt = _prepare_reader(repo, current, issue_text)
        path = current / "reader.md"
    elif role == "synthesizer":
        prompt = _prepare_synthesizer(current, issue_text)
        path = current / "synthesizer.md"
    elif role == "planner":
        prompt = run_core.build_planner_prompt_from_area_reader(
            current,
            issue_text,
            str(state.get("LocalCheck", "")),
            [str(value) for value in state.get("Labels", [])] if isinstance(state.get("Labels"), list) else [],
            str(state.get("StackContext", "")),
        )
        path = current / "planner.md"
    elif role == "implementer":
        prompt = render_template(
            _read_text(autodev_root / "promptTemplates" / "implementer.md"),
            {
                "StackContext": str(state.get("StackContext", "")),
                "LocalCheck": str(state.get("LocalCheck", "")),
                "Plan": _plan_text(current),
                "IssueText": issue_text,
            },
        )
        path = current / "implementer.md"
    elif role == "fixer":
        source = _fixer_source(current, arguments)
        prompt = _read_text(source)
        if not prompt.strip():
            raise OpenCodeAdapterError(f"fixer source artifact is empty: {source}")
        path = current / "fixer.md"
    elif role == "verifier":
        changed_files = collect_changed_files(repo)
        prompt = build_semantic_prompt(
            issue_text=issue_text,
            synthesized_handoff=_read_text(current / "synthesized-handoff.md"),
            plan=_plan_text(current),
            changed_files=changed_files,
            diff=collect_current_diff(repo, changed_files),
            deterministic_evidence=collect_deterministic_evidence(current),
            uncertainty_notes=_read_text(current / "verification-notes.md"),
            template=_read_text(autodev_root / "promptTemplates" / "semantic-verifier.md"),
        )
        path = current / "verifier.md"
    else:
        raise OpenCodeAdapterError(f"unsupported OpenCode role: {role}")

    effective = compose_prompt(role, prompt, policies[role])
    _write_text(path, effective)
    return path


def accept_role(role: str, repo: Path, input_path: Path | None = None) -> list[Path]:
    repo = repo.expanduser().resolve()
    current = repo / CURRENT_DIR
    if not current.is_dir():
        raise OpenCodeAdapterError(".codex-run/current is missing; prepare the role first")

    if role == "reader":
        source = input_path or current / "reader-brief.md"
        text = _bounded_result(source)
        reader_path = current / "reader-brief.md"
        handoff_path = current / "synthesized-handoff.md"
        _write_text(reader_path, text + "\n")
        _write_text(handoff_path, text + "\n")
        return [reader_path, handoff_path]
    if role == "synthesizer":
        source = input_path or current / "synthesized-handoff.md"
        text = _bounded_result(source)
        handoff_path = current / "synthesized-handoff.md"
        _write_text(handoff_path, text + "\n")
        return [handoff_path]
    if role == "planner":
        source = input_path or current / "plan.md"
        output = _bounded_result(source)
        target = current / "plan.md"
        handle_planner_output(output, target)
        return [target]
    if role == "implementer":
        target = current / "commit-message.txt"
        message = sanitize_model_output(_read_text(target)).splitlines()
        if not message or not message[0].strip():
            raise OpenCodeAdapterError("implementer must write .codex-run/current/commit-message.txt")
        _write_text(target, message[0].strip()[:200] + "\n")
        return [target]
    if role == "fixer":
        return []
    if role == "verifier":
        source = input_path or current / "verification-result.json"
        issue_text = _read_text(current / "issue.md")
        result = parse_semantic_output(
            _read_text(source),
            expected_criteria=extract_acceptance_criteria(issue_text) or None,
        )
        result_path = current / "verification-result.json"
        _write_text(result_path, json.dumps(result, indent=2, sort_keys=True) + "\n")
        attempt_path = write_semantic_result(current, _next_semantic_attempt(current), result)
        outputs = [result_path, attempt_path]
        final_path = current / "verification" / "final-verdict.json"
        if result["verdict"] in {"pass", "blocked"}:
            outputs.append(write_final_verdict(current, result))
        else:
            final_path.unlink(missing_ok=True)
        return outputs
    raise OpenCodeAdapterError(f"unsupported OpenCode role: {role}")


def workflow_stage(
    name: str,
    repo: Path,
    *,
    arguments: str = "",
    autodev_root: Path = AUTODEV_ROOT,
    attempt: int = 0,
    reason: str = "",
    runner=subprocess.run,
    which=shutil.which,
) -> tuple[int, dict[str, object]]:
    repo = repo.expanduser().resolve()
    autodev_root = autodev_root.expanduser().resolve()
    if attempt < 0:
        raise OpenCodeAdapterError("coordinator attempt must be zero or greater")

    if name == "preflight":
        if not repo.is_dir():
            raise OpenCodeAdapterError(f"target repository is not a directory: {repo}")
        if not (repo / ".git").exists():
            raise OpenCodeAdapterError(f"target repository is not a Git worktree: {repo}")
        workflow = autodev_root / "windows" / "scripts" / "issue-to-pr-cycle.ps1"
        if not workflow.is_file():
            raise OpenCodeAdapterError(f"AutoDev workflow is missing: {workflow}")
        missing = [tool for tool in ("pwsh", "gh", "git") if which(tool) is None]
        if missing:
            raise OpenCodeAdapterError("required command is unavailable: " + ", ".join(missing))
        missing_config = [
            name
            for name in ("GITHUB_OWNER", "GITHUB_REPO")
            if not os.environ.get(name, "").strip()
        ]
        if missing_config:
            raise OpenCodeAdapterError(
                "required AutoDev setting is unavailable: " + ", ".join(missing_config)
            )
        _configured_attempt_limit("MAX_REPAIR_ATTEMPTS", DEFAULT_MAX_REPAIR_ATTEMPTS)
        _configured_attempt_limit(
            "MAX_SEMANTIC_REPAIR_ATTEMPTS",
            DEFAULT_MAX_SEMANTIC_REPAIR_ATTEMPTS,
        )
        return 0, _stage_payload(
            repo,
            "CONTINUE",
            "preflight",
            requested_issue=issue_number_from_arguments(arguments),
            next_action="prepare the requested issue",
        )

    if name == "prepare":
        ensure_current_issue(repo, autodev_root, arguments, runner=runner)
        return 0, _stage_payload(
            repo,
            "CONTINUE",
            "prepare",
            next_action="delegate to autodev-reader",
        )

    current = repo / CURRENT_DIR
    state = _read_state(current)

    if name == "render-implementer":
        completed = _invoke_workflow_mode(repo, autodev_root, "RenderImplementerPrompt", runner=runner)
        if completed.returncode != 0:
            return 1, _command_failure_payload(repo, name, completed)
        if not (current / "implementer.md").is_file():
            raise OpenCodeAdapterError("RenderImplementerPrompt did not create implementer.md")
        return 0, _stage_payload(
            repo,
            "CONTINUE",
            name,
            next_action="delegate to autodev-implementer",
        )

    if name == "local-check":
        max_attempts = _configured_attempt_limit("MAX_REPAIR_ATTEMPTS", DEFAULT_MAX_REPAIR_ATTEMPTS)
        completed = _invoke_workflow_mode(repo, autodev_root, "LocalCheck", runner=runner)
        if completed.returncode == 0:
            return 0, _stage_payload(
                repo,
                "CONTINUE",
                name,
                next_action="run semantic verification",
                max_repair_attempts=max_attempts,
            )
        if completed.returncode == 10:
            if attempt >= max_attempts:
                return 0, _stage_payload(
                    repo,
                    "BLOCKED",
                    name,
                    reason="deterministic repair-attempt limit exhausted",
                    next_action="mark the run blocked",
                    max_repair_attempts=max_attempts,
                )
            return 0, _stage_payload(
                repo,
                "REPAIR",
                name,
                reason="deterministic verification failed",
                artifact=current / "local-repair.md",
                next_action="delegate the local repair to autodev-fixer, increment the attempt, then rerun local-check",
                max_repair_attempts=max_attempts,
            )
        return 1, _command_failure_payload(repo, name, completed)

    if name == "semantic":
        max_attempts = _configured_attempt_limit(
            "MAX_SEMANTIC_REPAIR_ATTEMPTS",
            DEFAULT_MAX_SEMANTIC_REPAIR_ATTEMPTS,
        )
        result_path = current / "verification-result.json"
        issue_text = _read_text(current / "issue.md")
        result = parse_semantic_output(
            _read_text(result_path),
            expected_criteria=extract_acceptance_criteria(issue_text) or None,
        )
        verdict = str(result["verdict"])
        if verdict == "pass":
            return 0, _stage_payload(
                repo,
                "CONTINUE",
                name,
                next_action="run commit/push/PR/CI",
                max_semantic_repair_attempts=max_attempts,
            )
        if verdict == "blocked":
            return 0, _stage_payload(
                repo,
                "BLOCKED",
                name,
                reason="semantic verifier blocked the run",
                next_action="mark the run blocked",
                max_semantic_repair_attempts=max_attempts,
            )
        if attempt >= max_attempts:
            return 0, _stage_payload(
                repo,
                "BLOCKED",
                name,
                reason="semantic repair-attempt limit exhausted",
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
        return 0, _stage_payload(
            repo,
            "REPAIR",
            name,
            reason=str(result.get("repair_brief", "semantic repair requested")),
            artifact=repair_path,
            next_action="delegate the semantic repair to autodev-fixer, increment the attempt, rerun local-check, then rerun autodev-verifier",
            max_semantic_repair_attempts=max_attempts,
        )

    if name == "pr-and-ci":
        max_attempts = _configured_attempt_limit("MAX_REPAIR_ATTEMPTS", DEFAULT_MAX_REPAIR_ATTEMPTS)
        completed = _invoke_workflow_mode(repo, autodev_root, "PrAndCi", runner=runner)
        if completed.returncode == 0:
            return 0, _stage_payload(
                repo,
                "CONTINUE",
                name,
                next_action="mark the PR ready for human review",
                max_repair_attempts=max_attempts,
            )
        if completed.returncode == 20:
            if attempt >= max_attempts:
                return 0, _stage_payload(
                    repo,
                    "BLOCKED",
                    name,
                    reason="CI repair-attempt limit exhausted",
                    next_action="mark the run blocked",
                    max_repair_attempts=max_attempts,
                )
            return 0, _stage_payload(
                repo,
                "REPAIR",
                name,
                reason="required PR checks failed",
                artifact=current / "ci-repair.md",
                next_action="delegate the CI repair to autodev-fixer, increment the attempt, rerun local-check and semantic verification, then retry pr-and-ci",
                max_repair_attempts=max_attempts,
            )
        return 1, _command_failure_payload(repo, name, completed)

    if name == "ready":
        if not str(state.get("PrUrl", "")).strip():
            raise OpenCodeAdapterError("cannot mark ready because state.json has no PR URL")
        completed = _invoke_workflow_mode(repo, autodev_root, "ReadyForReview", runner=runner)
        if completed.returncode != 0:
            return 1, _command_failure_payload(repo, name, completed)
        return 0, _stage_payload(
            repo,
            "PR_READY",
            name,
            next_action="human review; AutoDev never merges automatically",
        )

    if name == "blocked":
        completed = _invoke_workflow_mode(
            repo,
            autodev_root,
            "Blocked",
            message=reason or "OpenCode coordinator blocked the run.",
            runner=runner,
        )
        if completed.returncode != 0:
            return 1, _command_failure_payload(repo, name, completed)
        return 0, _stage_payload(
            repo,
            "BLOCKED",
            name,
            reason=reason,
            next_action="inspect the current AutoDev artifacts and intervene manually",
        )

    if name == "failed":
        if current.is_dir() and isinstance(_read_json(current / "state.json"), dict):
            completed = _invoke_workflow_mode(
                repo,
                autodev_root,
                "Blocked",
                message=reason or "OpenCode coordinator failed.",
                runner=runner,
            )
            if completed.returncode != 0:
                reason = reason or _command_failure_reason(completed)
        return 0, _stage_payload(
            repo,
            "FAILED",
            name,
            reason=reason or "OpenCode coordinator failed",
            next_action="inspect the failure artifacts, correct the setup/provider/subagent failure, then restart intentionally",
        )

    if name == "status":
        status = str(state.get("Status", ""))
        outcome = "PR_READY" if status == "ReadyForReview" else "BLOCKED" if status == "Blocked" else "CONTINUE"
        return 0, _stage_payload(
            repo,
            outcome,
            name,
            next_action="human review" if outcome == "PR_READY" else "continue from the current AutoDev stage",
        )

    raise OpenCodeAdapterError(f"unsupported coordinator stage: {name}")


def _invoke_workflow_mode(
    repo: Path,
    autodev_root: Path,
    mode: str,
    *,
    message: str = "",
    runner=subprocess.run,
):
    workflow = autodev_root / "windows" / "scripts" / "issue-to-pr-cycle.ps1"
    command = [
        "pwsh",
        "-NoProfile",
        "-File",
        str(workflow),
        "-Mode",
        mode,
        "-WorkingDirectory",
        str(repo),
    ]
    if message:
        command.extend(["-Message", message])
    return runner(
        command,
        cwd=repo,
        env=_workflow_environment(),
        text=True,
        capture_output=True,
        check=False,
    )


def _workflow_environment() -> dict[str, str]:
    env = dict(os.environ)
    for name in (
        "PROVIDER_PROFILE",
        "PLANNER_PROVIDER",
        "PLANNER_MODEL",
        "PLANNER_AGENT_COMMAND",
        "AGENT_PROVIDER",
        "AGENT_MODEL",
    ):
        env.pop(name, None)
    return env


def _configured_attempt_limit(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise OpenCodeAdapterError(f"{name} must be an integer") from exc
    if value < 0:
        raise OpenCodeAdapterError(f"{name} must be zero or greater")
    return value


def _stage_payload(
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
) -> dict[str, object]:
    current = repo / CURRENT_DIR
    state_value = _read_json(current / "state.json")
    state = state_value if isinstance(state_value, dict) else {}
    issue_number = int(state.get("IssueNumber", 0) or requested_issue or 0)
    payload: dict[str, object] = {
        "state": outcome,
        "issue_number": issue_number,
        "branch": str(state.get("BranchName", "")),
        "completed_stage": str(state.get("Status", "")),
        "failed_stage": stage if outcome in {"FAILED", "BLOCKED", "REPAIR"} else "",
        "stage": stage,
        "reason": _concise(reason),
        "artifact_dir": str(current),
        "artifact": str(artifact) if artifact is not None else "",
        "repository_modified": _repository_modified(repo),
        "commit_exists": bool(str(state.get("LastCommitSha", "")).strip()),
        "pr_exists": bool(str(state.get("PrUrl", "")).strip()),
        "pr_url": str(state.get("PrUrl", "")),
        "next_action": next_action,
    }
    if max_repair_attempts is not None:
        payload["max_repair_attempts"] = max_repair_attempts
    if max_semantic_repair_attempts is not None:
        payload["max_semantic_repair_attempts"] = max_semantic_repair_attempts
    return payload


def _command_failure_payload(repo: Path, stage: str, completed) -> dict[str, object]:
    return _stage_payload(
        repo,
        "FAILED",
        stage,
        reason=_command_failure_reason(completed),
        next_action="inspect the current AutoDev artifacts and command output before retrying",
    )


def _command_failure_reason(completed) -> str:
    return _concise((completed.stderr or completed.stdout or f"stage exited with {completed.returncode}").strip())


def _repository_modified(repo: Path) -> bool:
    try:
        return bool(collect_changed_files(repo))
    except (OSError, subprocess.SubprocessError, SemanticVerifierError):
        return False


def _concise(value: str, limit: int = 1000) -> str:
    return " ".join(str(value).split())[:limit]


def _next_semantic_attempt(current: Path) -> int:
    verification = current / "verification"
    attempts: list[int] = []
    for path in verification.glob("semantic-attempt-*.json") if verification.is_dir() else ():
        match = re.fullmatch(r"semantic-attempt-(\d+)\.json", path.name)
        if match:
            attempts.append(int(match.group(1)))
    return max(attempts, default=-1) + 1


def _prepare_reader(repo: Path, current: Path, issue_text: str) -> str:
    files, skipped_large, skipped_unreadable = area_reader_core.collect_repo_files(repo)
    repo_map = area_reader_core.build_repo_map(repo, files, skipped_large, skipped_unreadable)
    areas, routing = area_reader_core.route_areas(issue_text, "auto")
    facts = area_reader_core.detect_repo_facts(repo, files, areas, routing)
    groups = area_reader_core.build_verification_command_groups(facts, areas)
    recommendations = area_reader_core.recommended_command_groups(
        groups,
        issue_text=issue_text,
        changed_paths=(),
    )
    area_reader_core.apply_recommended_command_groups(groups, recommendations)
    _write_json(current / "routed-areas.json", {"areas": areas, **routing})
    _write_json(current / "detected-facts.json", facts)
    _write_json(current / "verification-command-groups.json", groups)
    _write_json(current / "recommended-command-groups.json", recommendations)

    bundle, included = _bounded_reader_bundle(repo, files, areas, repo_map)
    metadata = {
        "routed_areas": areas,
        "included_files": included,
        "bundle_chars": len(bundle),
        "max_chars": MAX_READER_BUNDLE_CHARS,
        "truncated": len(bundle) >= MAX_READER_BUNDLE_CHARS,
    }
    return area_reader_core.build_area_reader_prompt(
        issue_text,
        ",".join(areas) or "repository",
        bundle,
        metadata,
    )


def _bounded_reader_bundle(
    repo: Path,
    files: list[dict[str, object]],
    areas: list[str],
    repo_map: str,
) -> tuple[str, list[str]]:
    header = (
        "Routed areas: "
        + ", ".join(areas)
        + "\n\nRepository map:\n"
        + repo_map[:8_000]
        + "\nRelevant file excerpts:\n"
    )
    parts = [header]
    included: list[str] = []
    remaining = MAX_READER_BUNDLE_CHARS - len(header)
    for item in files:
        item_areas = item.get("areas", [])
        if not item.get("priority") and not any(area in item_areas for area in areas):
            continue
        relative = str(item.get("path", ""))
        if not relative:
            continue
        try:
            content = area_reader_core.read_file_for_bundle(repo, relative)
        except OSError:
            continue
        block = f"\n===== FILE: {relative} =====\n{content.rstrip()}\n"
        if remaining <= 0:
            break
        if len(block) > remaining:
            block = block[:remaining]
        parts.append(block)
        included.append(relative)
        remaining -= len(block)
    return "".join(parts)[:MAX_READER_BUNDLE_CHARS], included


def _prepare_synthesizer(current: Path, issue_text: str) -> str:
    brief = _read_text(current / "reader-brief.md") or _read_text(current / "synthesized-handoff.md")
    if not brief.strip():
        raise OpenCodeAdapterError("reader output is missing; run /autodev-read first")
    routed = _read_json(current / "routed-areas.json")
    areas = [str(value) for value in routed.get("areas", [])] if isinstance(routed, dict) else []
    facts = _read_json(current / "detected-facts.json")
    groups = _read_json(current / "verification-command-groups.json")
    return area_reader_core.build_synthesis_prompt(
        issue_text,
        areas,
        [{"area": "opencode-reader", "brief": brief[:MAX_HANDOFF_CHARS], "metadata": {"source": "reader-brief.md"}}],
        facts,
        groups,
    )


def _fixer_source(current: Path, arguments: str) -> Path:
    lowered = (arguments or "").casefold()
    preferred: list[Path] = []
    if "semantic" in lowered or "verifier" in lowered:
        preferred.append(current / "verification-repair.md")
    if "ci" in lowered:
        preferred.append(current / "ci-repair.md")
    if "local" in lowered or "deterministic" in lowered:
        preferred.append(current / "local-repair.md")
    preferred.extend(
        [
            current / "verification-repair.md",
            current / "local-repair.md",
            current / "ci-repair.md",
        ]
    )
    existing = []
    seen: set[Path] = set()
    for path in preferred:
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        existing.append(path)
    if not existing:
        raise OpenCodeAdapterError(
            "no repair artifact is available; use the existing AutoDev verification/local-check stage first"
        )
    if not any(token in lowered for token in ("semantic", "verifier", "ci", "local", "deterministic")):
        existing.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return existing[0]


def _resolved_policies(repo: Path, state: dict[str, object]) -> dict[str, str]:
    profile_value = str(state.get("ProviderProfile", "")).strip()
    if not profile_value:
        return resolve_prompt_policies({})
    profile = Path(profile_value).expanduser()
    if not profile.is_absolute():
        profile = repo / profile
    try:
        config = load_provider_config(str(profile))
    except (OSError, json.JSONDecodeError):
        config = {}
    return resolve_prompt_policies(config)


def _plan_text(current: Path) -> str:
    return _read_text(current / "plan.md") or _read_text(current / "coder-plan.md")


def _bounded_result(path: Path) -> str:
    value = sanitize_model_output(_read_text(path))
    if not value:
        raise OpenCodeAdapterError(f"role result is empty: {path}")
    if len(value) > MAX_HANDOFF_CHARS:
        raise OpenCodeAdapterError(
            f"role result exceeds the {MAX_HANDOFF_CHARS}-character AutoDev handoff limit: {path}"
        )
    return value


def _read_state(current: Path) -> dict[str, object]:
    state = _read_json(current / "state.json")
    if not isinstance(state, dict) or not state:
        raise OpenCodeAdapterError(".codex-run/current/state.json is missing or invalid")
    return state


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def _write_json(path: Path, value: object) -> None:
    _write_text(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Thin OpenCode frontend for existing AutoDev role artifacts.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    install = subparsers.add_parser("install")
    install.add_argument("--target-repo", default=".")
    install.add_argument("--autodev-root", default=str(AUTODEV_ROOT))
    install.add_argument("--python", default=os.environ.get("PYTHON", "python"))

    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--role", choices=("reader", "synthesizer", "planner", "implementer", "fixer", "verifier"), required=True)
    prepare.add_argument("--repo", default=".")
    prepare.add_argument("--arguments", default="")
    prepare.add_argument("--autodev-root", default=str(AUTODEV_ROOT))

    accept = subparsers.add_parser("accept")
    accept.add_argument("--role", choices=("reader", "synthesizer", "planner", "implementer", "fixer", "verifier"), required=True)
    accept.add_argument("--repo", default=".")
    accept.add_argument("--input", default="")

    stage = subparsers.add_parser("stage")
    stage.add_argument("--name", choices=COORDINATOR_STAGES, required=True)
    stage.add_argument("--repo", default=".")
    stage.add_argument("--arguments", default="")
    stage.add_argument("--autodev-root", default=str(AUTODEV_ROOT))
    stage.add_argument("--attempt", type=int, default=0)
    stage.add_argument("--reason", default="")
    return parser


def run(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "install":
            installed = install_assets(
                Path(args.target_repo),
                Path(args.autodev_root),
                python_command=args.python,
            )
            print(f"Installed {len(installed)} AutoDev OpenCode assets into {Path(args.target_repo).resolve() / '.opencode'}")
            return 0
        if args.command == "prepare":
            path = prepare_role(
                args.role,
                Path(args.repo),
                args.arguments,
                autodev_root=Path(args.autodev_root),
            )
            print(path)
            return 0
        if args.command == "accept":
            paths = accept_role(
                args.role,
                Path(args.repo),
                Path(args.input) if args.input else None,
            )
            for path in paths:
                print(path)
            return 0
        if args.command == "stage":
            try:
                code, payload = workflow_stage(
                    args.name,
                    Path(args.repo),
                    arguments=args.arguments,
                    autodev_root=Path(args.autodev_root),
                    attempt=args.attempt,
                    reason=args.reason,
                )
            except (OpenCodeAdapterError, PromptRunnerError, SemanticVerifierError, ProviderError, OSError, ValueError) as exc:
                payload = _stage_payload(
                    Path(args.repo).expanduser().resolve(),
                    "FAILED",
                    args.name,
                    reason=str(exc),
                    requested_issue=issue_number_from_arguments(args.arguments),
                    next_action="correct the reported setup or stage failure before retrying",
                )
                print(json.dumps(payload, sort_keys=True))
                return 1
            print(json.dumps(payload, sort_keys=True))
            return code
    except (OpenCodeAdapterError, PromptRunnerError, SemanticVerifierError, ProviderError, OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 1


def main() -> int:
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
