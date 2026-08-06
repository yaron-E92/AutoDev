from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from automation.model_output_sanitizer import sanitize_model_output
from automation.model_providers import ModelConfig, ModelProvider, ProviderError, create_provider, load_provider_config
from automation.model_roles import (
    ModelInvocationError,
    append_invocation_metadata,
    invoke_model,
    resolve_role_configs,
)
from automation.prompt_policies import compose_prompt, resolve_prompt_policies, role_policy_metadata


ALLOWED_VERDICTS = {"pass", "repair", "blocked"}
ALLOWED_REQUIREMENT_STATUSES = {"met", "missing", "uncertain"}
ALLOWED_FINDING_SEVERITIES = {"blocking", "warning"}
DEFAULT_MAX_SCHEMA_RETRIES = 1
DEFAULT_MAX_REPAIR_ATTEMPTS = 1
MAX_DIFF_CHARS = 120_000
MAX_EVIDENCE_CHARS = 30_000


class SemanticVerifierError(ProviderError):
    pass


@dataclass(frozen=True)
class SemanticSettings:
    enabled: bool
    max_schema_retries: int = DEFAULT_MAX_SCHEMA_RETRIES
    max_repair_attempts: int = DEFAULT_MAX_REPAIR_ATTEMPTS


def resolve_semantic_settings(
    file_config: dict[str, object],
    *,
    verifier_configured: bool,
) -> SemanticSettings:
    value = file_config.get("semantic_verification", {})
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise SemanticVerifierError(
            "semantic_verification must be an object",
            classification="invalid_config",
        )
    enabled = value.get("enabled", verifier_configured)
    if not isinstance(enabled, bool):
        raise SemanticVerifierError(
            "semantic_verification.enabled must be boolean",
            classification="invalid_config",
        )
    max_schema_retries = _non_negative_int(
        value.get("max_schema_retries", DEFAULT_MAX_SCHEMA_RETRIES),
        "semantic_verification.max_schema_retries",
    )
    max_repair_attempts = _non_negative_int(
        value.get("max_repair_attempts", DEFAULT_MAX_REPAIR_ATTEMPTS),
        "semantic_verification.max_repair_attempts",
    )
    if enabled and not verifier_configured:
        raise SemanticVerifierError(
            "semantic verification is enabled but the verifier role is not configured",
            classification="invalid_config",
        )
    return SemanticSettings(enabled, max_schema_retries, max_repair_attempts)


def extract_acceptance_criteria(issue_text: str) -> list[str]:
    lines = issue_text.splitlines()
    criteria: list[str] = []
    in_section = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#"):
            heading = stripped.lstrip("#").strip().casefold()
            if in_section and heading != "acceptance criteria":
                break
            in_section = heading == "acceptance criteria"
            continue
        if in_section and stripped.startswith(("- ", "* ")):
            criterion = stripped[2:].strip()
            if criterion:
                criteria.append(criterion)
    return criteria


def parse_semantic_output(output: str) -> dict[str, object]:
    cleaned = sanitize_model_output(output).strip()
    if not cleaned:
        raise SemanticVerifierError(
            "semantic verifier output was empty",
            classification="malformed_semantic_output",
        )
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise SemanticVerifierError(
            "semantic verifier output was not valid JSON",
            classification="malformed_semantic_output",
        ) from exc
    if not isinstance(value, dict):
        raise SemanticVerifierError(
            "semantic verifier output must be a JSON object",
            classification="malformed_semantic_output",
        )

    verdict = value.get("verdict")
    if verdict not in ALLOWED_VERDICTS:
        raise SemanticVerifierError(
            "semantic verifier verdict must be pass, repair, or blocked",
            classification="malformed_semantic_output",
        )

    raw_requirements = value.get("requirements")
    if not isinstance(raw_requirements, list):
        raise SemanticVerifierError(
            "semantic verifier requirements must be an array",
            classification="malformed_semantic_output",
        )
    requirements: list[dict[str, object]] = []
    for index, item in enumerate(raw_requirements):
        if not isinstance(item, dict):
            raise SemanticVerifierError(
                f"semantic verifier requirement {index} must be an object",
                classification="malformed_semantic_output",
            )
        criterion = item.get("criterion")
        status = item.get("status")
        evidence = item.get("evidence")
        if not isinstance(criterion, str) or not criterion.strip():
            raise SemanticVerifierError(
                f"semantic verifier requirement {index} has no criterion",
                classification="malformed_semantic_output",
            )
        if status not in ALLOWED_REQUIREMENT_STATUSES:
            raise SemanticVerifierError(
                f"semantic verifier requirement {index} has an invalid status",
                classification="malformed_semantic_output",
            )
        if not isinstance(evidence, list) or any(not isinstance(entry, str) for entry in evidence):
            raise SemanticVerifierError(
                f"semantic verifier requirement {index} evidence must be an array of strings",
                classification="malformed_semantic_output",
            )
        requirements.append(
            {
                "criterion": criterion.strip(),
                "status": status,
                "evidence": [entry.strip() for entry in evidence if entry.strip()],
            }
        )

    raw_findings = value.get("findings")
    if not isinstance(raw_findings, list):
        raise SemanticVerifierError(
            "semantic verifier findings must be an array",
            classification="malformed_semantic_output",
        )
    findings: list[dict[str, str]] = []
    for index, item in enumerate(raw_findings):
        if not isinstance(item, dict):
            raise SemanticVerifierError(
                f"semantic verifier finding {index} must be an object",
                classification="malformed_semantic_output",
            )
        severity = item.get("severity")
        message = item.get("message")
        path = item.get("path", "")
        if severity not in ALLOWED_FINDING_SEVERITIES:
            raise SemanticVerifierError(
                f"semantic verifier finding {index} has an invalid severity",
                classification="malformed_semantic_output",
            )
        if not isinstance(message, str) or not message.strip():
            raise SemanticVerifierError(
                f"semantic verifier finding {index} has no message",
                classification="malformed_semantic_output",
            )
        if not isinstance(path, str):
            raise SemanticVerifierError(
                f"semantic verifier finding {index} path must be text",
                classification="malformed_semantic_output",
            )
        findings.append(
            {
                "severity": severity,
                "message": message.strip(),
                "path": path.strip(),
            }
        )

    repair_brief = value.get("repair_brief", "")
    if not isinstance(repair_brief, str):
        raise SemanticVerifierError(
            "semantic verifier repair_brief must be text",
            classification="malformed_semantic_output",
        )

    blocking = [item for item in findings if item["severity"] == "blocking"]
    incomplete = [item for item in requirements if item["status"] != "met"]
    if verdict == "pass" and (blocking or incomplete):
        raise SemanticVerifierError(
            "semantic verifier returned pass with blocking or unmet requirements",
            classification="inconsistent_semantic_verdict",
        )
    if verdict == "repair" and not repair_brief.strip():
        raise SemanticVerifierError(
            "semantic verifier returned repair without a repair_brief",
            classification="inconsistent_semantic_verdict",
        )

    return {
        "verdict": verdict,
        "requirements": requirements,
        "findings": findings,
        "repair_brief": repair_brief.strip(),
    }


def build_schema_repair_prompt(original_prompt: str, invalid_output: str, error: str) -> str:
    return (
        original_prompt.rstrip()
        + "\n\nYour previous response was rejected because it did not match the required JSON schema.\n"
        + f"Validation error: {error}\n\n"
        + "Previous response:\n"
        + _bounded(invalid_output, 20_000)
        + "\n\nReturn corrected JSON only. Do not add Markdown fences or commentary.\n"
    )


def build_semantic_prompt(
    *,
    issue_text: str,
    synthesized_handoff: str,
    plan: str,
    changed_files: list[str],
    diff: str,
    deterministic_evidence: str,
    uncertainty_notes: str = "",
    template: str = "",
) -> str:
    criteria = extract_acceptance_criteria(issue_text)
    criteria_text = "\n".join(f"- {criterion}" for criterion in criteria) or "- No explicit acceptance-criteria section was detected. Infer only directly stated requirements."
    values = {
        "IssueText": _bounded(issue_text, MAX_EVIDENCE_CHARS),
        "AcceptanceCriteria": criteria_text,
        "SynthesizedHandoff": _bounded(synthesized_handoff, MAX_EVIDENCE_CHARS),
        "Plan": _bounded(plan, MAX_EVIDENCE_CHARS),
        "ChangedFiles": "\n".join(f"- {path}" for path in changed_files) or "- No changed files were detected.",
        "Diff": _bounded(diff, MAX_DIFF_CHARS),
        "DeterministicEvidence": _bounded(deterministic_evidence, MAX_EVIDENCE_CHARS),
        "UncertaintyNotes": _bounded(uncertainty_notes, 10_000) or "No additional uncertainty notes were recorded.",
    }
    if template:
        return render_template(template, values)
    return default_semantic_template(values)


def build_semantic_repair_prompt(
    *,
    issue_text: str,
    plan: str,
    semantic_result: dict[str, object],
    changed_files: list[str],
    diff: str,
    template: str = "",
) -> str:
    values = {
        "IssueText": _bounded(issue_text, MAX_EVIDENCE_CHARS),
        "Plan": _bounded(plan, MAX_EVIDENCE_CHARS),
        "VerificationFailure": json.dumps(semantic_result, indent=2, sort_keys=True),
        "RepairBrief": str(semantic_result.get("repair_brief", "")),
        "ChangedFiles": "\n".join(f"- {path}" for path in changed_files) or "- No changed files were detected.",
        "Diff": _bounded(diff, MAX_DIFF_CHARS),
    }
    if template:
        return render_template(template, values)
    return default_repair_template(values)


def render_template(template: str, values: dict[str, str]) -> str:
    rendered = template
    for key, value in values.items():
        rendered = rendered.replace("{{" + key + "}}", value)
    return rendered


def collect_changed_files(repo: Path) -> list[str]:
    changed = _git_lines(repo, ["git", "diff", "--name-only", "--relative", "--", "."])
    staged = _git_lines(repo, ["git", "diff", "--cached", "--name-only", "--relative", "--", "."])
    untracked = _git_lines(repo, ["git", "ls-files", "--others", "--exclude-standard"])
    return sorted(set(changed + staged + untracked))


def collect_current_diff(repo: Path, changed_files: list[str] | None = None) -> str:
    tracked = _git_text(repo, ["git", "diff", "--no-ext-diff", "--binary", "HEAD", "--", "."])
    changed_files = changed_files if changed_files is not None else collect_changed_files(repo)
    untracked_blocks: list[str] = []
    for relative in changed_files:
        path = repo / relative
        if not path.is_file() or _is_tracked(repo, relative):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            text = "[binary or unreadable file]"
        untracked_blocks.append(
            f"diff --git a/{relative} b/{relative}\n"
            f"new file mode 100644\n--- /dev/null\n+++ b/{relative}\n"
            + "\n".join("+" + line for line in _bounded(text, 20_000).splitlines())
        )
    return _bounded("\n".join(part for part in [tracked, *untracked_blocks] if part), MAX_DIFF_CHARS)


def collect_deterministic_evidence(current_dir: Path) -> str:
    parts: list[str] = []
    for name in (
        "verification-result-summary.md",
        "local-check.log",
        "recommended-command-groups.json",
        "ci-summary.json",
    ):
        path = current_dir / name
        if path.is_file():
            parts.append(f"## {name}\n{_bounded(path.read_text(encoding='utf-8'), 12_000)}")
    return "\n\n".join(parts) or "No deterministic evidence artifact was available."


def semantic_artifact_path(out_dir: Path, attempt: int) -> Path:
    return out_dir / "verification" / f"semantic-attempt-{attempt}.json"


def write_semantic_result(out_dir: Path, attempt: int, result: dict[str, object]) -> Path:
    path = semantic_artifact_path(out_dir, attempt)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def write_final_verdict(out_dir: Path, result: dict[str, object]) -> Path:
    path = out_dir / "verification" / "final-verdict.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def safe_semantic_metadata(settings: SemanticSettings) -> dict[str, object]:
    return {
        "enabled": settings.enabled,
        "max_schema_retries": settings.max_schema_retries,
        "max_repair_attempts": settings.max_repair_attempts,
    }


def invoke_semantic_verifier(
    *,
    provider: ModelProvider,
    config: ModelConfig,
    prompt: str,
    telemetry_path: Path | None,
    policies: dict[str, str],
    max_schema_retries: int,
    response_writer: Callable[[int, str], None] | None = None,
) -> dict[str, object]:
    current_prompt = compose_prompt("verifier", prompt, policies["verifier"])
    for attempt in range(max_schema_retries + 1):
        try:
            output, record = invoke_model(provider, config, current_prompt, role="verifier", attempt=attempt)
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
            return parse_semantic_output(output)
        except SemanticVerifierError as exc:
            if attempt >= max_schema_retries:
                raise
            current_prompt = compose_prompt(
                "verifier",
                build_schema_repair_prompt(prompt, output, str(exc)),
                policies["verifier"],
            )
    raise SemanticVerifierError(
        "semantic verifier did not produce a valid result",
        classification="malformed_semantic_output",
    )


def prepare_semantic_prompt(repo: Path, current_dir: Path, template_path: Path, output_path: Path) -> None:
    state = _read_json(current_dir / "state.json")
    issue_text = _read_text(current_dir / "issue.md") or str(state.get("IssueText", ""))
    plan = _read_text(current_dir / "plan.md")
    handoff = _read_text(current_dir / "synthesized-handoff.md")
    changed_files = collect_changed_files(repo)
    prompt = build_semantic_prompt(
        issue_text=issue_text,
        synthesized_handoff=handoff,
        plan=plan,
        changed_files=changed_files,
        diff=collect_current_diff(repo, changed_files),
        deterministic_evidence=collect_deterministic_evidence(current_dir),
        uncertainty_notes=_read_text(current_dir / "verification-notes.md"),
        template=_read_text(template_path),
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(prompt, encoding="utf-8")


def prepare_semantic_repair_prompt(repo: Path, current_dir: Path, template_path: Path, output_path: Path) -> None:
    state = _read_json(current_dir / "state.json")
    result = _read_json(current_dir / "verification-result.json")
    changed_files = collect_changed_files(repo)
    prompt = build_semantic_repair_prompt(
        issue_text=_read_text(current_dir / "issue.md") or str(state.get("IssueText", "")),
        plan=_read_text(current_dir / "plan.md"),
        semantic_result=result,
        changed_files=changed_files,
        diff=collect_current_diff(repo, changed_files),
        template=_read_text(template_path),
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(prompt, encoding="utf-8")
    (current_dir / "verification" / "repair-brief.md").parent.mkdir(parents=True, exist_ok=True)
    (current_dir / "verification" / "repair-brief.md").write_text(
        str(result.get("repair_brief", "")).strip() + "\n",
        encoding="utf-8",
    )


def resolve_profile_roles(profile_path: Path) -> tuple[dict[str, object], dict[str, ModelConfig | None]]:
    file_config = load_provider_config(str(profile_path))
    defaults = {
        "reader": {"provider": "mock", "model": "reader"},
        "coder": {"provider": "mock", "model": "coder"},
    }
    roles = resolve_role_configs(defaults=defaults, file_config=file_config)
    return file_config, roles


def default_semantic_template(values: dict[str, str]) -> str:
    return f"""You are the independent semantic verifier. Review only; do not edit files or redesign the solution.

Original issue:
{values['IssueText']}

Detectable acceptance criteria:
{values['AcceptanceCriteria']}

Synthesized repository handoff:
{values['SynthesizedHandoff']}

Implementation plan:
{values['Plan']}

Changed files:
{values['ChangedFiles']}

Current diff:
{values['Diff']}

Deterministic verification evidence:
{values['DeterministicEvidence']}

Uncertainty or skipped-check notes:
{values['UncertaintyNotes']}

Return JSON only with verdict pass, repair, or blocked; requirements using met, missing, or uncertain; findings using blocking or warning; and a targeted repair_brief. Warnings alone do not block.
"""


def default_repair_template(values: dict[str, str]) -> str:
    return f"""You are the fixer. Make only the targeted correction identified by the independent verifier.

Issue:
{values['IssueText']}

Plan:
{values['Plan']}

Verifier result:
{values['VerificationFailure']}

Repair brief:
{values['RepairBrief']}

Changed files:
{values['ChangedFiles']}

Current diff:
{values['Diff']}

Return NO_CHANGES_REQUIRED only when the repository already satisfies the repair brief; otherwise return BEGIN_UNIFIED_DIFF, an applicable unified diff, and END_UNIFIED_DIFF.
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build and validate AutoDev semantic verification artifacts.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    enabled = subparsers.add_parser("enabled")
    enabled.add_argument("--provider-profile", required=True)

    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--repo", required=True)
    prepare.add_argument("--current-dir", required=True)
    prepare.add_argument("--template", required=True)
    prepare.add_argument("--out", required=True)

    repair = subparsers.add_parser("repair-prompt")
    repair.add_argument("--repo", required=True)
    repair.add_argument("--current-dir", required=True)
    repair.add_argument("--template", required=True)
    repair.add_argument("--out", required=True)

    validate = subparsers.add_parser("validate")
    validate.add_argument("--input", required=True)
    validate.add_argument("--output", required=True)

    verdict = subparsers.add_parser("verdict")
    verdict.add_argument("--input", required=True)
    return parser


def run(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "enabled":
            file_config, roles = resolve_profile_roles(Path(args.provider_profile))
            settings = resolve_semantic_settings(file_config, verifier_configured=roles.get("verifier") is not None)
            return 0 if settings.enabled else 1
        if args.command == "prepare":
            prepare_semantic_prompt(Path(args.repo), Path(args.current_dir), Path(args.template), Path(args.out))
            return 0
        if args.command == "repair-prompt":
            prepare_semantic_repair_prompt(Path(args.repo), Path(args.current_dir), Path(args.template), Path(args.out))
            return 0
        if args.command == "validate":
            result = parse_semantic_output(Path(args.input).read_text(encoding="utf-8"))
            Path(args.output).parent.mkdir(parents=True, exist_ok=True)
            Path(args.output).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            return 0
        if args.command == "verdict":
            result = parse_semantic_output(Path(args.input).read_text(encoding="utf-8"))
            print(result["verdict"])
            return {"pass": 0, "repair": 10, "blocked": 20}[str(result["verdict"])]
    except (OSError, SemanticVerifierError, ProviderError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 2


def _git_lines(repo: Path, argv: list[str]) -> list[str]:
    return [line.strip() for line in _git_text(repo, argv).splitlines() if line.strip()]


def _git_text(repo: Path, argv: list[str]) -> str:
    completed = subprocess.run(argv, cwd=repo, text=True, capture_output=True, check=False)
    if completed.returncode != 0:
        raise SemanticVerifierError(
            f"semantic evidence command failed: {' '.join(argv)}",
            classification="evidence_collection_failed",
        )
    return completed.stdout


def _is_tracked(repo: Path, relative: str) -> bool:
    completed = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "--", relative],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )
    return completed.returncode == 0


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _read_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _bounded(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    digest = hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()
    return value[:limit] + f"\n[truncated; sha256={digest}]\n"


def _non_negative_int(value: object, label: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise SemanticVerifierError(f"{label} must be an integer", classification="invalid_config") from exc
    if parsed < 0:
        raise SemanticVerifierError(f"{label} must be zero or greater", classification="invalid_config")
    return parsed


if __name__ == "__main__":
    raise SystemExit(run())
