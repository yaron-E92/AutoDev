from __future__ import annotations

from pathlib import Path
from typing import Callable
from automation.provider_contract import ModelConfig, ModelProvider, ProviderError
from automation.model_roles import (
    ModelInvocationError,
    append_invocation_metadata,
    invoke_model,
)
from automation.prompt_policies import compose_prompt, role_policy_metadata

from automation.semantic_configuration import (
    _bounded_count,
)
from automation.semantic_contract import (
    MAX_SCHEMA_RETRIES,
    SemanticVerifierError,
)
from automation.semantic_evidence import (
    collect_changed_files,
    collect_cross_file_regression_evidence,
    collect_current_diff,
    collect_deterministic_evidence,
)
from automation.semantic_prompts import (
    build_schema_repair_prompt,
    build_semantic_prompt,
    build_semantic_repair_prompt,
)
from automation.semantic_schema import (
    _malformed,
    parse_semantic_output,
)
from automation.semantic_storage import (
    _read_json,
    _read_text,
)

def invoke_semantic_verifier(
    *,
    provider: ModelProvider,
    config: ModelConfig,
    prompt: str,
    telemetry_path: Path | None,
    policies: dict[str, str],
    max_schema_retries: int,
    response_writer: Callable[[int, str], None] | None = None,
    expected_criteria: list[str] | None = None,
) -> dict[str, object]:
    max_schema_retries = _bounded_count(
        max_schema_retries,
        "max_schema_retries",
        MAX_SCHEMA_RETRIES,
    )
    current_prompt = compose_prompt("verifier", prompt, policies["verifier"])
    for attempt in range(max_schema_retries + 1):
        try:
            output, record = invoke_model(
                provider,
                config,
                current_prompt,
                role="verifier",
                attempt=attempt,
            )
        except ModelInvocationError as exc:
            exc.record.update(role_policy_metadata("verifier", policies))
            if telemetry_path is not None:
                append_invocation_metadata(telemetry_path, exc.record)
            raise
        record.update(role_policy_metadata("verifier", policies))
        if telemetry_path is not None:
            append_invocation_metadata(telemetry_path, record)
        if response_writer is not None:
            response_writer(attempt, output)
        try:
            return parse_semantic_output(
                output,
                expected_criteria=expected_criteria,
            )
        except SemanticVerifierError as exc:
            if attempt >= max_schema_retries:
                raise
            current_prompt = compose_prompt(
                "verifier",
                build_schema_repair_prompt(
                    prompt,
                    output,
                    str(exc),
                    expected_criteria=expected_criteria,
                ),
                policies["verifier"],
            )
    raise _malformed("semantic verifier did not produce a valid result")

def prepare_semantic_prompt(
    repo: Path,
    current_dir: Path,
    template_path: Path,
    output_path: Path,
) -> None:
    state = _read_json(current_dir / "state.json")
    issue_text = _read_text(current_dir / "issue.md") or str(
        state.get("IssueText", "")
    )
    changed_files = collect_changed_files(repo)
    diff = collect_current_diff(repo, changed_files)
    prompt = build_semantic_prompt(
        issue_text=issue_text,
        synthesized_handoff=_read_text(current_dir / "synthesized-handoff.md"),
        plan=_read_text(current_dir / "plan.md"),
        changed_files=changed_files,
        diff=diff,
        deterministic_evidence=collect_deterministic_evidence(current_dir),
        cross_file_regression_evidence=collect_cross_file_regression_evidence(repo, changed_files, diff),
        uncertainty_notes=_read_text(current_dir / "verification-notes.md"),
        template=_read_text(template_path),
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(prompt, encoding="utf-8")

def prepare_semantic_repair_prompt(
    repo: Path,
    current_dir: Path,
    template_path: Path,
    output_path: Path,
) -> None:
    state = _read_json(current_dir / "state.json")
    result = _read_json(current_dir / "verification-result.json")
    changed_files = collect_changed_files(repo)
    prompt = build_semantic_repair_prompt(
        issue_text=_read_text(current_dir / "issue.md")
        or str(state.get("IssueText", "")),
        plan=_read_text(current_dir / "plan.md"),
        semantic_result=result,
        changed_files=changed_files,
        diff=collect_current_diff(repo, changed_files),
        template=_read_text(template_path),
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(prompt, encoding="utf-8")
    repair_brief_path = current_dir / "verification" / "repair-brief.md"
    repair_brief_path.parent.mkdir(parents=True, exist_ok=True)
    repair_brief_path.write_text(
        str(result.get("repair_brief", "")).strip() + "\n",
        encoding="utf-8",
    )
