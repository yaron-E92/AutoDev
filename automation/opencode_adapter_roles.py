from __future__ import annotations

from automation import opencode_resume_contract

from automation import opencode_resume_checkpoint

import json
from pathlib import Path
from automation.model_output_sanitizer import sanitize_model_output
from automation.prompt_policies import compose_prompt, resolve_prompt_policies
from automation.prompt_runner import (
    REQUIRED_PLAN_HEADINGS,
    PromptRunnerError,
    handle_planner_output,
)
from automation.semantic_artifacts import write_final_verdict, write_semantic_result
from automation.semantic_contract import SemanticVerifierError
from automation.semantic_evidence import collect_changed_files, collect_cross_file_regression_evidence, collect_current_diff, collect_deterministic_evidence
from automation.semantic_prompts import build_schema_repair_prompt, build_semantic_prompt, extract_acceptance_criteria
from automation.semantic_schema import parse_semantic_output, semantic_result_template
from automation.semantic_text import render_template

from automation.opencode_adapter_contract import (
    AUTODEV_ROOT,
    CURRENT_DIR,
    OpenCodeAdapterError,
    role_contracts,
)
from automation.opencode_adapter_handoff import (
    build_planner_prompt_from_area_reader,
    _bounded_result,
    _bounded_text,
    _fixer_source,
    _next_semantic_attempt,
    _plan_text,
    _prepare_reader,
    _prepare_synthesizer,
    _write_plan_template,
)
from automation.opencode_adapter_models import (
    reject_unsupported_model_overrides,
    resolve_opencode_model_mappings,
)
from automation.opencode_adapter_protocol import (
    _begin_role_invocation,
    _contract_output_path,
    _ensure_opencode_protocol,
    _mark_role_accepted,
    _reset_current_correction,
    _resolved_policies,
    _write_role_contracts,
    ensure_current_issue,
)
from automation.opencode_adapter_storage import (
    _read_diagnostics,
    _read_state,
    _read_text,
    _write_diagnostics,
    _write_json,
    _write_text,
)

def prepare_role(
    role: str,
    repo: Path,
    arguments: str,
    *,
    autodev_root: Path = AUTODEV_ROOT,
) -> Path:
    reject_unsupported_model_overrides(arguments)
    repo = repo.expanduser().resolve()
    autodev_root = autodev_root.expanduser().resolve()
    current = ensure_current_issue(repo, autodev_root, arguments)
    _ensure_opencode_protocol(current)
    _begin_role_invocation(current, role)
    opencode_resume_checkpoint.begin_role(repo, role, arguments)
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
        prompt = build_planner_prompt_from_area_reader(
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
            "Use `.autodev-run/current/plan.template.md` as the exact six-section structure. "
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
            "Write one concise commit-message line to `.autodev-run/current/commit-message.txt` "
            "and run the exact accept command from `.autodev-run/current/role-contracts.json`.\n"
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
        diff = collect_current_diff(repo, changed_files)
        prompt = build_semantic_prompt(
            issue_text=issue_text,
            synthesized_handoff=_read_text(current / "synthesized-handoff.md"),
            plan=_plan_text(current),
            changed_files=changed_files,
            diff=diff,
            deterministic_evidence=collect_deterministic_evidence(current),
            cross_file_regression_evidence=collect_cross_file_regression_evidence(repo, changed_files, diff),
            uncertainty_notes=_read_text(current / "verification-notes.md"),
            template=_read_text(autodev_root / "promptTemplates" / "semantic-verifier.md"),
        )
        prompt += (
            "\n\nAutoDev deterministic JSON contract:\n"
            "Start from `.autodev-run/current/verification-result.template.json`. Preserve every pre-populated "
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
        raise OpenCodeAdapterError(".autodev-run/current is missing; prepare the role first")
    _write_role_contracts(current)
    try:
        outputs = _accept_role_once(role, current, input_path)
    except (OpenCodeAdapterError, PromptRunnerError, SemanticVerifierError) as exc:
        _raise_contract_rejection(current, role, input_path, exc)
    _mark_role_accepted(current, role, outputs)
    _reset_current_correction(current, role)
    if opencode_resume_contract.has_manifest(repo):
        mappings = resolve_opencode_model_mappings(repo)
        opencode_resume_checkpoint.checkpoint_role(repo, role, outputs, mappings)
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
            raise OpenCodeAdapterError("implementer must write .autodev-run/current/commit-message.txt")
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
    correction = current / f"contract-correction-{role}.md"

    if role == "verifier":
        issue_text = _read_text(current / "issue.md")
        schema_correction = build_schema_repair_prompt(
            "",
            previous,
            str(error),
            expected_criteria=extract_acceptance_criteria(issue_text) or None,
        )
        correction_body = (
            "# AutoDev protocol correction\n\n"
            "This is the only protocol-format correction attempt allowed for the current verifier invocation.\n\n"
            "Exact role contract:\n\n```json\n"
            + json.dumps(contract, indent=2, sort_keys=True)
            + "\n```\n\n"
            + schema_correction
            + f"\nAfter correcting `.autodev-run/current/verification-result.json`, rerun exactly:\n\n`{contract.get('accept', '')}`\n"
        )
    else:
        template = _read_text(current / "plan.template.md") if role == "planner" else ""
        correction_body = (
            "# AutoDev protocol correction\n\n"
            "This is the only protocol-format correction attempt allowed for the current role invocation.\n\n"
            f"Role: `{role}`\n\n"
            f"Validation errors:\n\n```text\n{error}\n```\n\n"
            "Exact role contract:\n\n```json\n"
            + json.dumps(contract, indent=2, sort_keys=True)
            + "\n```\n\n"
            + ("Exact generated template:\n\n```text\n" + template + "\n```\n\n" if template else "")
            + ("Bounded previous output:\n\n```text\n" + previous + "\n```\n\n" if previous else "")
            + f"Correct the designated output artifact once, then rerun exactly:\n\n`{contract.get('accept', '')}`\n"
        )
    _write_text(correction, correction_body)
    raise OpenCodeAdapterError(
        f"{role} protocol artifact rejected; one correction is allowed using {correction}; "
        f"then rerun exactly: {contract.get('accept', '')}"
    ) from error
