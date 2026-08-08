from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from area_reader_v2 import runner_core as area_reader_core
from automation import run_real_issue_core as run_core
from automation import workflow_stages
from automation.model_output_sanitizer import sanitize_model_output
from automation.model_providers import ProviderError, load_provider_config
from automation.prompt_policies import compose_prompt, resolve_prompt_policies
from automation.prompt_runner import (
    REQUIRED_PLAN_HEADINGS,
    PromptRunnerError,
    handle_planner_output,
)
from automation.semantic_verifier import (
    SemanticVerifierError,
    build_semantic_prompt,
    collect_changed_files,
    collect_current_diff,
    collect_deterministic_evidence,
    extract_acceptance_criteria,
    parse_semantic_output,
    render_template,
    semantic_result_template,
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
ROLE_NAMES = ("reader", "synthesizer", "planner", "implementer", "fixer", "verifier")
COORDINATOR_STAGES = workflow_stages.STAGES
MAX_HANDOFF_CHARS = 30_000
MAX_READER_BUNDLE_CHARS = 24_000
OPENCODE_PROTOCOL_VERSION = 1
DEFAULT_MAX_REPAIR_ATTEMPTS = workflow_stages.DEFAULT_MAX_REPAIR_ATTEMPTS
DEFAULT_MAX_SEMANTIC_REPAIR_ATTEMPTS = workflow_stages.DEFAULT_MAX_SEMANTIC_REPAIR_ATTEMPTS


class OpenCodeAdapterError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        classification: str = workflow_stages.FAILURE_DETERMINISTIC,
    ) -> None:
        super().__init__(message)
        self.classification = classification


def role_contracts() -> dict[str, dict[str, object]]:
    return {
        "reader": {
            "input_artifact": ".codex-run/current/reader.md",
            "output_artifact": ".codex-run/current/reader-brief.md",
            "format": "bounded text/Markdown handoff",
            "max_chars": MAX_HANDOFF_CHARS,
            "prepare": "python .opencode/autodev.py prepare --role reader",
            "accept": "python .opencode/autodev.py accept --role reader --input .codex-run/current/reader-brief.md",
        },
        "synthesizer": {
            "input_artifact": ".codex-run/current/synthesizer.md",
            "output_artifact": ".codex-run/current/synthesized-handoff.md",
            "format": "bounded text/Markdown cross-area handoff",
            "max_chars": MAX_HANDOFF_CHARS,
            "prepare": "python .opencode/autodev.py prepare --role synthesizer",
            "accept": "python .opencode/autodev.py accept --role synthesizer --input .codex-run/current/synthesized-handoff.md",
        },
        "planner": {
            "input_artifact": ".codex-run/current/planner.md",
            "template_artifact": ".codex-run/current/plan.template.md",
            "output_artifact": ".codex-run/current/plan.md",
            "format": "exact six-section AutoDev plan",
            "required_sections": list(REQUIRED_PLAN_HEADINGS),
            "max_chars": MAX_HANDOFF_CHARS,
            "prepare": "python .opencode/autodev.py prepare --role planner",
            "accept": "python .opencode/autodev.py accept --role planner --input .codex-run/current/plan.md",
        },
        "implementer": {
            "input_artifact": ".codex-run/current/implementer.md",
            "output_artifact": ".codex-run/current/commit-message.txt",
            "format": "one non-empty commit-message line, maximum 200 characters",
            "max_chars": 200,
            "prepare": "standalone only: python .opencode/autodev.py prepare --role implementer",
            "coordinator_prepare": "none; stage --name render-implementer already rendered implementer.md",
            "accept": "python .opencode/autodev.py accept --role implementer",
        },
        "fixer": {
            "input_artifact": "one of local-repair.md, verification-repair.md, ci-repair.md selected by prepare --role fixer --arguments local|semantic|ci",
            "output_artifact": "target repository edits only",
            "format": "targeted source repair; no new AutoDev protocol artifact",
            "prepare": "python .opencode/autodev.py prepare --role fixer --arguments local|semantic|ci",
            "accept": "python .opencode/autodev.py accept --role fixer",
        },
        "verifier": {
            "input_artifact": ".codex-run/current/verifier.md",
            "template_artifact": ".codex-run/current/verification-result.template.json",
            "output_artifact": ".codex-run/current/verification-result.json",
            "format": "strict semantic JSON using only parser-supported fields/enums and exact pre-populated acceptance criteria",
            "prepare": "python .opencode/autodev.py prepare --role verifier",
            "accept": "python .opencode/autodev.py accept --role verifier --input .codex-run/current/verification-result.json",
        },
    }


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

    target.mkdir(parents=True, exist_ok=True)
    for wrapper_name in ("autodev.py", "autodev.ps1"):
        wrapper_source = source / wrapper_name
        if not wrapper_source.is_file():
            raise OpenCodeAdapterError(f"missing canonical OpenCode bridge wrapper: {wrapper_source}")
        wrapper_target = target / wrapper_name
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
    return workflow_stages.issue_number_from_arguments(arguments)


def ensure_current_issue(
    repo: Path,
    autodev_root: Path,
    arguments: str,
    *,
    runner=subprocess.run,
) -> Path:
    try:
        return workflow_stages.ensure_prepared_issue(
            repo.expanduser().resolve(),
            arguments,
            autodev_root=autodev_root.expanduser().resolve(),
            runner=runner,
        )
    except workflow_stages.WorkflowStageError as exc:
        raise OpenCodeAdapterError(
            str(exc),
            classification=exc.classification,
        ) from exc


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
    _ensure_opencode_protocol(current)
    _begin_role_invocation(current, role)
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
        _write_plan_template(current)
        prompt = run_core.build_planner_prompt_from_area_reader(
            current,
            issue_text,
            str(state.get("LocalCheck", "")),
            [str(value) for value in state.get("Labels", [])]
            if isinstance(state.get("Labels"), list)
            else [],
            str(state.get("StackContext", "")),
        )
        prompt += (
            "\n\nAutoDev deterministic output contract:\n"
            "Use `.codex-run/current/plan.template.md` as the exact six-section structure. "
            "Do not add preamble, scratchpad, or extra top-level sections.\n"
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
        prompt += (
            "\n\nAutoDev deterministic output contract:\n"
            "Write one concise commit-message line to `.codex-run/current/commit-message.txt` "
            "and run the exact accept command from `.codex-run/current/role-contracts.json`.\n"
        )
        path = current / "implementer.md"
    elif role == "fixer":
        source = _fixer_source(current, arguments)
        prompt = _read_text(source)
        if not prompt.strip():
            raise OpenCodeAdapterError(f"fixer source artifact is empty: {source}")
        prompt += (
            "\n\nAutoDev deterministic output contract:\n"
            "Apply only this repair. Do not create workflow state, commits, branches, PRs, or issue mutations. "
            "Run exactly `python .opencode/autodev.py accept --role fixer` when the targeted edit is complete.\n"
        )
        path = current / "fixer.md"
    elif role == "verifier":
        criteria = extract_acceptance_criteria(issue_text)
        _write_json(
            current / "verification-result.template.json",
            semantic_result_template(criteria),
            ensure_ascii=False,
        )
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
        prompt += (
            "\n\nAutoDev deterministic JSON contract:\n"
            "Start from `.codex-run/current/verification-result.template.json`. Preserve every pre-populated "
            "criterion verbatim. Use only verdict pass|repair|blocked, requirement status "
            "met|missing|uncertain, and finding severity blocking|warning. Evidence must be string arrays; "
            "findings may be [] for a clean pass. Return/write JSON only.\n"
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
    _write_role_contracts(current)
    try:
        outputs = _accept_role_once(role, current, input_path)
    except (OpenCodeAdapterError, PromptRunnerError, SemanticVerifierError) as exc:
        _raise_contract_rejection(current, role, input_path, exc)
    _mark_role_accepted(current, role, outputs)
    _reset_current_correction(current, role)
    return outputs


def _accept_role_once(role: str, current: Path, input_path: Path | None) -> list[Path]:
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
    try:
        code, payload = workflow_stages.execute_stage(
            name,
            repo,
            arguments=arguments,
            autodev_root=autodev_root,
            attempt=attempt,
            reason=reason,
            runner=runner,
            which=which,
        )
    except workflow_stages.WorkflowStageError as exc:
        raise OpenCodeAdapterError(
            str(exc),
            classification=exc.classification,
        ) from exc

    current = repo / CURRENT_DIR
    if name == "prepare" and payload.get("state") == "CONTINUE" and current.is_dir():
        _ensure_opencode_protocol(current)
    elif name == "render-implementer" and payload.get("state") == "CONTINUE" and current.is_dir():
        _ensure_opencode_protocol(current)
        _begin_role_invocation(current, "implementer")
    return code, payload


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
        [
            {
                "area": "opencode-reader",
                "brief": brief[:MAX_HANDOFF_CHARS],
                "metadata": {"source": "reader-brief.md"},
            }
        ],
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
    existing: list[Path] = []
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


def _ensure_opencode_protocol(current: Path) -> None:
    state = _read_state(current)
    state["OpenCodeProtocolVersion"] = OPENCODE_PROTOCOL_VERSION
    if not isinstance(state.get("AcceptedRoleArtifacts"), dict):
        state["AcceptedRoleArtifacts"] = {}
    _write_json(current / "state.json", state)
    _write_role_contracts(current)
    diagnostics = _read_diagnostics(current)
    diagnostics.setdefault("role_invocations", {})
    diagnostics.setdefault("protocol_correction_attempts", {})
    diagnostics.setdefault("protocol_correction_used", {})
    diagnostics.setdefault("stage_invocations", {})
    diagnostics.setdefault("stage_wall_time_ms", {})
    diagnostics.setdefault("repeated_identical_failures", 0)
    _write_diagnostics(current, diagnostics)


def _write_role_contracts(current: Path) -> None:
    _write_json(
        current / "role-contracts.json",
        {
            "version": OPENCODE_PROTOCOL_VERSION,
            "roles": role_contracts(),
            "protocol_correction_limit": 1,
        },
    )


def _write_plan_template(current: Path) -> None:
    _write_text(
        current / "plan.template.md",
        "\n\n".join(REQUIRED_PLAN_HEADINGS) + "\n",
    )


def _begin_role_invocation(current: Path, role: str) -> None:
    if role not in ROLE_NAMES:
        raise OpenCodeAdapterError(f"unsupported OpenCode role: {role}")
    state = _read_state(current)
    accepted = state.get("AcceptedRoleArtifacts", {})
    if isinstance(accepted, dict):
        accepted.pop(role, None)
        state["AcceptedRoleArtifacts"] = accepted
        _write_json(current / "state.json", state)
    diagnostics = _read_diagnostics(current)
    invocations = diagnostics.setdefault("role_invocations", {})
    if isinstance(invocations, dict):
        invocations[role] = int(invocations.get(role, 0) or 0) + 1
    used = diagnostics.setdefault("protocol_correction_used", {})
    if isinstance(used, dict):
        used[role] = False
    _write_diagnostics(current, diagnostics)
    (current / f"contract-correction-{role}.md").unlink(missing_ok=True)


def _mark_role_accepted(current: Path, role: str, outputs: list[Path]) -> None:
    state_value = _read_json(current / "state.json")
    if not isinstance(state_value, dict) or not state_value:
        return
    state = state_value
    contract = role_contracts().get(role, {})
    relative = str(contract.get("output_artifact", ""))
    path = current / Path(relative).name if relative.startswith(".codex-run/current/") else None
    digest = _file_sha256(path) if path is not None else ""
    accepted = state.setdefault("AcceptedRoleArtifacts", {})
    if isinstance(accepted, dict):
        accepted[role] = {
            "artifact": relative,
            "sha256": digest,
        }
    state["OpenCodeProtocolVersion"] = OPENCODE_PROTOCOL_VERSION
    _write_json(current / "state.json", state)


def _raise_contract_rejection(
    current: Path,
    role: str,
    input_path: Path | None,
    error: BaseException,
) -> None:
    diagnostics = _read_diagnostics(current)
    used = diagnostics.setdefault("protocol_correction_used", {})
    already_used = bool(used.get(role, False)) if isinstance(used, dict) else False
    if already_used:
        raise OpenCodeAdapterError(
            f"{role} protocol correction limit exhausted after one retry: {error}"
        ) from error

    if isinstance(used, dict):
        used[role] = True
    attempts = diagnostics.setdefault("protocol_correction_attempts", {})
    if isinstance(attempts, dict):
        attempts[role] = int(attempts.get(role, 0) or 0) + 1
    _write_diagnostics(current, diagnostics)

    contract = role_contracts().get(role, {})
    source = input_path or _contract_output_path(current, role)
    previous = _bounded_text(_read_text(source), 8_000) if source is not None else ""
    template = ""
    if role == "planner":
        template = _read_text(current / "plan.template.md")
    elif role == "verifier":
        template = _read_text(current / "verification-result.template.json")
    correction = current / f"contract-correction-{role}.md"
    _write_text(
        correction,
        "# AutoDev protocol correction\n\n"
        "This is the only protocol-format correction attempt allowed for the current role invocation.\n\n"
        f"Role: `{role}`\n\n"
        f"Validation errors:\n\n```text\n{error}\n```\n\n"
        "Exact role contract:\n\n```json\n"
        + json.dumps(contract, indent=2, sort_keys=True)
        + "\n```\n\n"
        + ("Exact generated template:\n\n```text\n" + template + "\n```\n\n" if template else "")
        + ("Bounded previous output:\n\n```text\n" + previous + "\n```\n\n" if previous else "")
        + f"Correct the designated output artifact once, then rerun exactly:\n\n`{contract.get('accept', '')}`\n",
    )
    raise OpenCodeAdapterError(
        f"{role} protocol artifact rejected; one correction is allowed using {correction}; "
        f"then rerun exactly: {contract.get('accept', '')}"
    ) from error


def _reset_current_correction(current: Path, role: str) -> None:
    diagnostics = _read_diagnostics(current)
    used = diagnostics.setdefault("protocol_correction_used", {})
    if isinstance(used, dict):
        used[role] = False
    _write_diagnostics(current, diagnostics)


def _contract_output_path(current: Path, role: str) -> Path | None:
    relative = str(role_contracts().get(role, {}).get("output_artifact", ""))
    if relative.startswith(".codex-run/current/"):
        return current / Path(relative).name
    return None


def _read_diagnostics(current: Path) -> dict[str, object]:
    value = _read_json(current / workflow_stages.DIAGNOSTICS_FILE)
    return value if isinstance(value, dict) else {}


def _write_diagnostics(current: Path, value: dict[str, object]) -> None:
    _write_json(current / workflow_stages.DIAGNOSTICS_FILE, value)


def _file_sha256(path: Path | None) -> str:
    if path is None:
        return ""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


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


def _bounded_text(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    digest = hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()
    return value[:limit] + f"\n[truncated; sha256={digest}]\n"


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


def _write_json(path: Path, value: object, *, ensure_ascii: bool = True) -> None:
    _write_text(
        path,
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=ensure_ascii) + "\n",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Thin OpenCode frontend for existing AutoDev role artifacts.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    install = subparsers.add_parser("install")
    install.add_argument("--target-repo", default=".")
    install.add_argument("--autodev-root", default=str(AUTODEV_ROOT))
    install.add_argument("--python", default=os.environ.get("PYTHON", "python"))

    prepare = subparsers.add_parser("prepare")
    prepare.add_argument(
        "--role",
        choices=ROLE_NAMES,
        required=True,
    )
    prepare.add_argument("--repo", default=".")
    prepare.add_argument("--arguments", default="")
    prepare.add_argument("--autodev-root", default=str(AUTODEV_ROOT))

    accept = subparsers.add_parser("accept")
    accept.add_argument(
        "--role",
        choices=ROLE_NAMES,
        required=True,
    )
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
            print(
                f"Installed {len(installed)} AutoDev OpenCode assets into "
                f"{Path(args.target_repo).resolve() / '.opencode'}"
            )
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
            repo = Path(args.repo).expanduser().resolve()
            try:
                code, payload = workflow_stage(
                    args.name,
                    repo,
                    arguments=args.arguments,
                    autodev_root=Path(args.autodev_root),
                    attempt=args.attempt,
                    reason=args.reason,
                )
            except (
                OpenCodeAdapterError,
                PromptRunnerError,
                SemanticVerifierError,
                ProviderError,
                workflow_stages.WorkflowStageError,
                OSError,
                ValueError,
            ) as exc:
                payload = workflow_stages.record_stage_failure(
                    repo,
                    args.name,
                    exc,
                    requested_issue=issue_number_from_arguments(args.arguments),
                )
                print(json.dumps(payload, sort_keys=True))
                return 1
            print(json.dumps(payload, sort_keys=True))
            return code
    except (
        OpenCodeAdapterError,
        PromptRunnerError,
        SemanticVerifierError,
        ProviderError,
        workflow_stages.WorkflowStageError,
        OSError,
        ValueError,
    ) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 1


def main() -> int:
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
