from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from automation.model_output_sanitizer import sanitize_model_output
from automation.model_providers import ModelConfig, ModelProvider, ProviderError, load_provider_config
from automation.model_roles import (
    ModelInvocationError,
    append_invocation_metadata,
    invoke_model,
    resolve_role_configs,
)
from automation.prompt_policies import compose_prompt, role_policy_metadata


ALLOWED_VERDICTS = {"pass", "repair", "blocked"}
ALLOWED_REQUIREMENT_STATUSES = {"met", "missing", "uncertain"}
ALLOWED_FINDING_SEVERITIES = {"blocking", "warning"}
DEFAULT_MAX_SCHEMA_RETRIES = 1
DEFAULT_MAX_REPAIR_ATTEMPTS = 1
MAX_SCHEMA_RETRIES = 1
MAX_REPAIR_ATTEMPTS = 1
MAX_DIFF_CHARS = 120_000
MAX_EVIDENCE_CHARS = 30_000
MAX_REGRESSION_EVIDENCE_CHARS = 12_000
MAX_REGRESSION_SYMBOLS = 16
MAX_REGRESSION_REFERENCES = 24
MAX_REGRESSION_FILE_BYTES = 300_000
SEMANTIC_SOURCE_SUFFIXES = {
    ".cs",
    ".cshtml",
    ".razor",
    ".xaml",
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".java",
    ".kt",
    ".kts",
    ".go",
    ".rs",
    ".cpp",
    ".cc",
    ".c",
    ".h",
    ".hpp",
}
SEMANTIC_IGNORED_PARTS = {
    ".git",
    ".autodev-run",
    "bin",
    "obj",
    "node_modules",
    "dist",
    "build",
    "coverage",
    ".vs",
    ".idea",
    ".vscode",
    ".venv",
    "venv",
    "__pycache__",
}
_TEMPLATE_PLACEHOLDER = re.compile(
    r"\{~\{(?P<new>[A-Za-z][A-Za-z0-9_]*)\}~\}"
    r"|\{\{(?P<legacy>[A-Za-z][A-Za-z0-9_]*)\}\}"
)
_LEGACY_ONLY_PLACEHOLDERS = {"LocalCheck", "StackContext"}
_DECLARATION_PATTERNS = (
    re.compile(r"\b(?:class|interface|record|struct|enum|def|function|func)\s+([A-Za-z_][A-Za-z0-9_]*)"),
    re.compile(
        r"\b(?:public|protected|internal|export)\s+"
        r"(?:(?:static|virtual|override|abstract|sealed|async|readonly|const|partial|required|new)\s+)*"
        r"(?:[A-Za-z_][A-Za-z0-9_<>,?.\[\]]*\s+)+"
        r"([A-Za-z_][A-Za-z0-9_]*)\s*(?=\{|=>|\(|=|;)"
    ),
    re.compile(r"\b(?:const|let|var)\s+([A-Za-z_][A-Za-z0-9_]*)"),
)


class SemanticVerifierError(ProviderError):
    pass


class ChangedFileList(list[str]):
    def __init__(self, values: list[str], repo: Path) -> None:
        super().__init__(values)
        self.repo = repo


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
        raise _config_error("semantic_verification must be an object")

    enabled = value.get("enabled", verifier_configured)
    if not isinstance(enabled, bool):
        raise _config_error("semantic_verification.enabled must be boolean")

    max_schema_retries = _bounded_count(
        value.get("max_schema_retries", DEFAULT_MAX_SCHEMA_RETRIES),
        "semantic_verification.max_schema_retries",
        MAX_SCHEMA_RETRIES,
    )
    max_repair_attempts = _bounded_count(
        value.get("max_repair_attempts", DEFAULT_MAX_REPAIR_ATTEMPTS),
        "semantic_verification.max_repair_attempts",
        MAX_REPAIR_ATTEMPTS,
    )
    if enabled and not verifier_configured:
        raise _config_error(
            "semantic verification is enabled but the verifier role is not configured"
        )
    return SemanticSettings(enabled, max_schema_retries, max_repair_attempts)


def extract_acceptance_criteria(issue_text: str) -> list[str]:
    criteria: list[str] = []
    in_section = False
    for line in issue_text.splitlines():
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


def semantic_result_template(expected_criteria: list[str] | None = None) -> dict[str, object]:
    """Return a parser-compatible, fail-safe semantic result skeleton.

    The blocked/uncertain defaults prevent an untouched template from being mistaken
    for approval while keeping every enum value valid for parse_semantic_output().
    """
    return {
        "verdict": "blocked",
        "requirements": [
            {
                "criterion": criterion,
                "status": "uncertain",
                "evidence": [],
            }
            for criterion in (expected_criteria or [])
        ],
        "findings": [],
        "repair_brief": "",
    }


def parse_semantic_output(
    output: str,
    *,
    expected_criteria: list[str] | None = None,
) -> dict[str, object]:
    cleaned = sanitize_model_output(output).strip()
    if not cleaned:
        raise _malformed("semantic verifier output was empty")
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise _malformed("semantic verifier output was not valid JSON") from exc

    schema_errors = _semantic_schema_errors(value)
    if schema_errors:
        raise _malformed(
            "semantic verifier output schema errors: " + "; ".join(schema_errors)
        )
    assert isinstance(value, dict)

    verdict = str(value["verdict"])
    requirements = _parse_requirements(value["requirements"])
    findings = _parse_findings(value["findings"])
    repair_brief = str(value.get("repair_brief", ""))

    if expected_criteria:
        reported = {str(item["criterion"]).strip().casefold() for item in requirements}
        missing = [
            criterion
            for criterion in expected_criteria
            if criterion.strip().casefold() not in reported
        ]
        if missing:
            raise SemanticVerifierError(
                "semantic verifier omitted acceptance criteria: " + "; ".join(missing),
                classification="incomplete_semantic_requirements",
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


def build_schema_repair_prompt(
    original_prompt: str,
    invalid_output: str,
    error: str,
    *,
    expected_criteria: list[str] | None = None,
) -> str:
    template = json.dumps(
        semantic_result_template(expected_criteria),
        indent=2,
        ensure_ascii=False,
    )
    return (
        original_prompt.rstrip()
        + "\n\nYour previous response was rejected because it did not match the required JSON contract.\n"
        + f"Validation errors: {error}\n\n"
        + "Previous response:\n"
        + _bounded(invalid_output, 20_000)
        + "\n\nCorrect the complete artifact once. Start from this exact parser-compatible template, "
        + "preserve every pre-populated criterion verbatim, and use only these values: "
        + "verdict=pass|repair|blocked, status=met|missing|uncertain, "
        + "severity=blocking|warning. A clean pass may use findings: [].\n"
        + template
        + "\nDo not add Markdown fences or commentary.\n"
    )


def build_semantic_prompt(
    *,
    issue_text: str,
    synthesized_handoff: str,
    plan: str,
    changed_files: list[str],
    diff: str,
    deterministic_evidence: str,
    cross_file_regression_evidence: str = "",
    uncertainty_notes: str = "",
    template: str = "",
) -> str:
    criteria = extract_acceptance_criteria(issue_text)
    if not cross_file_regression_evidence:
        source_repo = getattr(changed_files, "repo", None)
        if isinstance(source_repo, Path):
            cross_file_regression_evidence = collect_cross_file_regression_evidence(
                source_repo,
                list(changed_files),
                diff,
            )
    values = {
        "IssueText": _bounded(issue_text, MAX_EVIDENCE_CHARS),
        "AcceptanceCriteria": (
            "\n".join(f"- {criterion}" for criterion in criteria)
            or "- No explicit acceptance-criteria section was detected. Infer only directly stated requirements."
        ),
        "SynthesizedHandoff": _bounded(synthesized_handoff, MAX_EVIDENCE_CHARS),
        "Plan": _bounded(plan, MAX_EVIDENCE_CHARS),
        "ChangedFiles": (
            "\n".join(f"- {path}" for path in changed_files)
            or "- No changed files were detected."
        ),
        "Diff": _bounded(diff, MAX_DIFF_CHARS),
        "DeterministicEvidence": _bounded(
            deterministic_evidence,
            MAX_EVIDENCE_CHARS,
        ),
        "CrossFileRegressionEvidence": (
            _bounded(cross_file_regression_evidence, MAX_REGRESSION_EVIDENCE_CHARS)
            or "No removed/changed declaration references were detected in unchanged source files."
        ),
        "UncertaintyNotes": (
            _bounded(uncertainty_notes, 10_000)
            or "No additional uncertainty notes were recorded."
        ),
    }
    return render_template(template, values) if template else default_semantic_template(values)


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
        "ChangedFiles": (
            "\n".join(f"- {path}" for path in changed_files)
            or "- No changed files were detected."
        ),
        "Diff": _bounded(diff, MAX_DIFF_CHARS),
    }
    return render_template(template, values) if template else default_repair_template(values)


def render_template(template: str, values: dict[str, str]) -> str:
    unresolved: set[str] = set()
    for match in _TEMPLATE_PLACEHOLDER.finditer(template):
        key = match.group("new") or match.group("legacy")
        if key not in values and key not in _LEGACY_ONLY_PLACEHOLDERS:
            unresolved.add(match.group(0))
    if unresolved:
        raise SemanticVerifierError(
            "semantic verifier prompt contains unresolved placeholders: "
            + ", ".join(sorted(unresolved)),
            classification="unresolved_semantic_placeholders",
        )

    def replacement(match: re.Match[str]) -> str:
        key = match.group("new") or match.group("legacy")
        return values.get(key, match.group(0))

    return _TEMPLATE_PLACEHOLDER.sub(replacement, template)


def collect_changed_files(repo: Path) -> list[str]:
    values = sorted(
        set(
            _git_lines(repo, ["git", "diff", "--name-only", "--relative", "--", "."])
            + _git_lines(
                repo,
                ["git", "diff", "--cached", "--name-only", "--relative", "--", "."],
            )
            + _git_lines(repo, ["git", "ls-files", "--others", "--exclude-standard"])
        )
    )
    return ChangedFileList(values, repo.expanduser().resolve())


def collect_current_diff(
    repo: Path,
    changed_files: list[str] | None = None,
) -> str:
    tracked = _git_text(
        repo,
        ["git", "diff", "--no-ext-diff", "--binary", "HEAD", "--", "."],
    )
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
    return _bounded(
        "\n".join(part for part in [tracked, *untracked_blocks] if part),
        MAX_DIFF_CHARS,
    )


def collect_cross_file_regression_evidence(
    repo: Path,
    changed_files: list[str] | None = None,
    diff: str | None = None,
) -> str:
    changed_files = changed_files if changed_files is not None else collect_changed_files(repo)
    diff = diff if diff is not None else collect_current_diff(repo, changed_files)
    symbols = _removed_symbol_candidates(diff)
    if not symbols:
        return "No removed/changed declaration-like identifiers were detected in the current diff."

    changed = {path.replace("\\", "/") for path in changed_files}
    references: list[str] = []
    for path in repo.rglob("*"):
        if len(references) >= MAX_REGRESSION_REFERENCES:
            break
        if not path.is_file() or path.suffix.casefold() not in SEMANTIC_SOURCE_SUFFIXES:
            continue
        relative = path.relative_to(repo).as_posix()
        if relative in changed or any(part in SEMANTIC_IGNORED_PARTS for part in Path(relative).parts):
            continue
        try:
            if path.stat().st_size > MAX_REGRESSION_FILE_BYTES:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line_number, line in enumerate(text.splitlines(), start=1):
            for symbol in symbols:
                if re.search(r"\b" + re.escape(symbol) + r"\b", line):
                    references.append(
                        f"- {symbol} -> {relative}:{line_number}: {_bounded(line.strip(), 240)}"
                    )
                    break
            if len(references) >= MAX_REGRESSION_REFERENCES:
                break

    lines = ["Removed/changed declaration candidates:"]
    lines.extend(f"- {symbol}" for symbol in symbols)
    lines.append("")
    lines.append("References in unchanged source files:")
    if references:
        lines.extend(references)
    else:
        lines.append("- No unchanged-file references to the bounded candidates were found.")
    lines.append("")
    lines.append(
        "Verifier instruction: treat a removed/changed symbol that is still referenced by unchanged code as a potential blocking regression unless deterministic evidence proves the reference remains valid."
    )
    return _bounded("\n".join(lines), MAX_REGRESSION_EVIDENCE_CHARS)


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
            parts.append(
                f"## {name}\n{_bounded(path.read_text(encoding='utf-8'), 12_000)}"
            )
    return "\n\n".join(parts) or "No deterministic evidence artifact was available."


def semantic_artifact_path(out_dir: Path, attempt: int) -> Path:
    return out_dir / "verification" / f"semantic-attempt-{attempt}.json"


def write_semantic_result(
    out_dir: Path,
    attempt: int,
    result: dict[str, object],
) -> Path:
    path = semantic_artifact_path(out_dir, attempt)
    _write_result_pair(path, result, f"Semantic Verification Attempt {attempt}")
    return path


def write_final_verdict(
    out_dir: Path,
    result: dict[str, object],
) -> Path:
    path = out_dir / "verification" / "final-verdict.json"
    _write_result_pair(path, result, "Final Semantic Verdict")
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


def resolve_profile_roles(
    profile_path: Path,
) -> tuple[dict[str, object], dict[str, ModelConfig | None]]:
    file_config = load_provider_config(str(profile_path))
    roles = resolve_role_configs(
        defaults={
            "reader": {"provider": "mock", "model": "reader"},
            "coder": {"provider": "mock", "model": "coder"},
        },
        file_config=file_config,
    )
    return file_config, roles


def default_semantic_template(values: dict[str, str]) -> str:
    return f"""You are the independent semantic verifier. Review only; do not edit files.

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

Cross-file regression evidence:
{values['CrossFileRegressionEvidence']}

Uncertainty or skipped-check notes:
{values['UncertaintyNotes']}

Explicitly check removed or changed public/cross-file symbols against unchanged references before returning pass. Return JSON only with verdict pass, repair, or blocked; requirements using met, missing, or uncertain; findings using blocking or warning; and a targeted repair_brief. Warnings alone do not block.
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


def render_semantic_summary(
    result: dict[str, object],
    title: str,
) -> str:
    lines = [
        f"# {title}",
        "",
        f"Verdict: {result.get('verdict', 'unknown')}",
        "",
        "## Requirements",
        "",
    ]
    for requirement in result.get("requirements", []):
        if not isinstance(requirement, dict):
            continue
        lines.append(
            f"- **{requirement.get('status', 'unknown')}** — "
            f"{requirement.get('criterion', '')}"
        )
        for evidence in requirement.get("evidence", []):
            lines.append(f"  - Evidence: {evidence}")
    lines.extend(["", "## Findings", ""])
    findings = result.get("findings", [])
    if not findings:
        lines.append("- None.")
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        path = f" ({finding.get('path')})" if finding.get("path") else ""
        lines.append(
            f"- **{finding.get('severity', 'unknown')}** — "
            f"{finding.get('message', '')}{path}"
        )
    repair_brief = str(result.get("repair_brief", "")).strip()
    lines.extend(["", "## Repair Brief", "", repair_brief or "None.", ""])
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build and validate AutoDev semantic verification artifacts."
    )
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
            settings = resolve_semantic_settings(
                file_config,
                verifier_configured=roles.get("verifier") is not None,
            )
            return 0 if settings.enabled else 1
        if args.command == "prepare":
            prepare_semantic_prompt(
                Path(args.repo),
                Path(args.current_dir),
                Path(args.template),
                Path(args.out),
            )
            return 0
        if args.command == "repair-prompt":
            prepare_semantic_repair_prompt(
                Path(args.repo),
                Path(args.current_dir),
                Path(args.template),
                Path(args.out),
            )
            return 0
        if args.command == "validate":
            result = parse_semantic_output(
                Path(args.input).read_text(encoding="utf-8")
            )
            output = Path(args.output)
            _write_result_pair(output, result, "Semantic Verification Result")
            return 0
        if args.command == "verdict":
            result = parse_semantic_output(
                Path(args.input).read_text(encoding="utf-8")
            )
            print(result["verdict"])
            return {"pass": 0, "repair": 10, "blocked": 20}[str(result["verdict"])]
    except (OSError, SemanticVerifierError, ProviderError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 2


def _semantic_schema_errors(value: object) -> list[str]:
    if not isinstance(value, dict):
        return ["top-level value must be a JSON object"]

    errors: list[str] = []
    verdict = value.get("verdict")
    if verdict not in ALLOWED_VERDICTS:
        errors.append("verdict must be pass, repair, or blocked")

    requirements = value.get("requirements")
    if not isinstance(requirements, list):
        errors.append("requirements must be an array")
    else:
        for index, item in enumerate(requirements):
            if not isinstance(item, dict):
                errors.append(f"requirement {index} must be an object")
                continue
            criterion = item.get("criterion")
            status = item.get("status")
            evidence = item.get("evidence")
            if not isinstance(criterion, str) or not criterion.strip():
                errors.append(f"requirement {index} criterion must be non-empty text")
            if status not in ALLOWED_REQUIREMENT_STATUSES:
                errors.append(
                    f"requirement {index} status must be met, missing, or uncertain"
                )
            if not isinstance(evidence, list) or any(
                not isinstance(entry, str) for entry in evidence
            ):
                errors.append(f"requirement {index} evidence must be a string array")

    findings = value.get("findings")
    if not isinstance(findings, list):
        errors.append("findings must be an array")
    else:
        for index, item in enumerate(findings):
            if not isinstance(item, dict):
                errors.append(f"finding {index} must be an object")
                continue
            severity = item.get("severity")
            message = item.get("message")
            path = item.get("path", "")
            if severity not in ALLOWED_FINDING_SEVERITIES:
                errors.append(f"finding {index} severity must be blocking or warning")
            if not isinstance(message, str) or not message.strip():
                errors.append(f"finding {index} message must be non-empty text")
            if not isinstance(path, str):
                errors.append(f"finding {index} path must be text")

    repair_brief = value.get("repair_brief", "")
    if not isinstance(repair_brief, str):
        errors.append("repair_brief must be text")
    return errors


def _parse_requirements(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise _malformed("semantic verifier requirements must be an array")
    requirements: list[dict[str, object]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise _malformed(f"semantic verifier requirement {index} must be an object")
        criterion = item.get("criterion")
        status = item.get("status")
        evidence = item.get("evidence")
        if not isinstance(criterion, str) or not criterion.strip():
            raise _malformed(f"semantic verifier requirement {index} has no criterion")
        if status not in ALLOWED_REQUIREMENT_STATUSES:
            raise _malformed(
                f"semantic verifier requirement {index} has an invalid status"
            )
        if not isinstance(evidence, list) or any(
            not isinstance(entry, str) for entry in evidence
        ):
            raise _malformed(
                f"semantic verifier requirement {index} evidence must be an array of strings"
            )
        requirements.append(
            {
                "criterion": criterion.strip(),
                "status": status,
                "evidence": [
                    entry.strip() for entry in evidence if entry.strip()
                ],
            }
        )
    return requirements


def _parse_findings(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list):
        raise _malformed("semantic verifier findings must be an array")
    findings: list[dict[str, str]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise _malformed(f"semantic verifier finding {index} must be an object")
        severity = item.get("severity")
        message = item.get("message")
        path = item.get("path", "")
        if severity not in ALLOWED_FINDING_SEVERITIES:
            raise _malformed(f"semantic verifier finding {index} has an invalid severity")
        if not isinstance(message, str) or not message.strip():
            raise _malformed(f"semantic verifier finding {index} has no message")
        if not isinstance(path, str):
            raise _malformed(f"semantic verifier finding {index} path must be text")
        findings.append(
            {
                "severity": severity,
                "message": message.strip(),
                "path": path.strip(),
            }
        )
    return findings


def _write_result_pair(
    json_path: Path,
    result: dict[str, object],
    title: str,
) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    json_path.with_suffix(".md").write_text(
        render_semantic_summary(result, title),
        encoding="utf-8",
    )


def _removed_symbol_candidates(diff: str) -> list[str]:
    candidates: list[str] = []
    seen: set[str] = set()
    for raw in diff.splitlines():
        if not raw.startswith("-") or raw.startswith("---"):
            continue
        line = raw[1:].strip()
        for pattern in _DECLARATION_PATTERNS:
            match = pattern.search(line)
            if not match:
                continue
            symbol = match.group(1)
            if len(symbol) < 3 or symbol in seen:
                continue
            seen.add(symbol)
            candidates.append(symbol)
            if len(candidates) >= MAX_REGRESSION_SYMBOLS:
                return candidates
    return candidates


def _git_lines(repo: Path, argv: list[str]) -> list[str]:
    return [line.strip() for line in _git_text(repo, argv).splitlines() if line.strip()]


def _git_text(repo: Path, argv: list[str]) -> str:
    completed = subprocess.run(
        argv,
        cwd=repo,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        evidence = (completed.stderr or completed.stdout or "").strip()
        raise SemanticVerifierError(
            f"semantic evidence command failed ({completed.returncode}): {' '.join(argv)}: "
            f"{_bounded(evidence, 1000)}",
            classification="evidence_collection_failed",
        )
    return completed.stdout or ""


def _is_tracked(repo: Path, relative: str) -> bool:
    completed = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "--", relative],
        cwd=repo,
        text=True,
        encoding="utf-8",
        errors="replace",
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


def _bounded_count(value: object, label: str, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise _config_error(f"{label} must be an integer") from exc
    if parsed < 0 or parsed > maximum:
        raise _config_error(f"{label} must be between 0 and {maximum}")
    return parsed


def _malformed(message: str) -> SemanticVerifierError:
    return SemanticVerifierError(
        message,
        classification="malformed_semantic_output",
    )


def _config_error(message: str) -> SemanticVerifierError:
    return SemanticVerifierError(message, classification="invalid_config")


if __name__ == "__main__":
    raise SystemExit(run())
