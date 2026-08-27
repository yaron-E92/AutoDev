from __future__ import annotations

import json
import re
from pathlib import Path

from automation import run_manifest, workflow_stages


REPORT_NAME = "non-success-report.md"
REPORT_RELATIVE = f".autodev-run/current/{REPORT_NAME}"
OPERATION_REPORT_RELATIVE = f".autodev-run/last-operation/{REPORT_NAME}"
NON_SUCCESS_STATES = {"FAILED", "BLOCKED", "WAITING", "REPAIR"}
MAX_EVIDENCE_CHARS = 2400

_SECRET_PATTERNS = (
    re.compile(r"(?i)\b(Bearer)\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(
        r"(?i)\b(authorization|api[_-]?key|token|secret|password|cookie|proxy[_-]?authorization)"
        r"\b\s*[:=]\s*([^\s,;]+)"
    ),
    re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
)


def redact(value: object) -> str:
    text = str(value or "")
    text = _SECRET_PATTERNS[0].sub(r"\1 <redacted>", text)
    text = _SECRET_PATTERNS[1].sub(r"\1=<redacted>", text)
    text = _SECRET_PATTERNS[2].sub("<redacted>", text)
    return text


def update_report(repo: Path, payload: dict[str, object]) -> tuple[dict[str, object], str]:
    repo = repo.expanduser().resolve()
    result = dict(payload)
    current = repo / workflow_stages.CURRENT_DIR
    detached_operation = (
        int(result.get("requested_issue_number", 0) or 0) > 0
        and result.get("new_run_prepared") is False
    )
    relative = OPERATION_REPORT_RELATIVE if detached_operation else REPORT_RELATIVE
    path = (
        repo / ".autodev-run" / "last-operation" / REPORT_NAME
        if detached_operation
        else current / REPORT_NAME
    )
    state_name = str(result.get("state", ""))

    if state_name == "PR_READY":
        (current / REPORT_NAME).unlink(missing_ok=True)
        (repo / ".autodev-run" / "last-operation" / REPORT_NAME).unlink(
            missing_ok=True
        )
        result.pop("non_success_report", None)
        result.pop("non_success_report_error", None)
        return result, ""
    if state_name not in NON_SUCCESS_STATES:
        return result, ""

    try:
        text = render_report(repo, result)
        _write_atomic(path, text)
        result["non_success_report"] = relative
        result.pop("non_success_report_error", None)
        return result, relative
    except Exception as exc:  # best-effort diagnostic artifact must never mask the primary outcome
        result["non_success_report_error"] = redact(exc)[:500]
        return result, ""


def render_report(repo: Path, payload: dict[str, object]) -> str:
    repo = repo.expanduser().resolve()
    current = repo / workflow_stages.CURRENT_DIR
    detached_operation = (
        int(payload.get("requested_issue_number", 0) or 0) > 0
        and payload.get("new_run_prepared") is False
    )
    if detached_operation:
        state: dict[str, object] = {}
        diagnostics: dict[str, object] = {}
        manifest: dict[str, object] = {}
    else:
        state_value = workflow_stages.read_json(current / "state.json")
        state = state_value if isinstance(state_value, dict) else {}
        diagnostics_value = workflow_stages.read_json(
            current / workflow_stages.DIAGNOSTICS_FILE
        )
        diagnostics = diagnostics_value if isinstance(diagnostics_value, dict) else {}
        manifest = _load_manifest(repo)

    outcome = str(payload.get("state", ""))
    issue_number = int(payload.get("issue_number", 0) or state.get("IssueNumber", 0) or 0)
    branch = str(payload.get("branch", "") or state.get("BranchName", ""))
    commit = str(payload.get("commit_sha", "") or state.get("LastCommitSha", ""))
    pr_url = str(payload.get("pr_url", "") or state.get("PrUrl", ""))
    pr_head = str(payload.get("pr_head_sha", "") or state.get("PrHeadSha", ""))
    failed_stage = str(
        payload.get("failed_stage", "")
        or payload.get("completed_stage", "")
        or _manifest_failure(manifest).get("stage", "")
        or _manifest_current_stage(manifest)
    )
    classification = str(
        payload.get("failure_classification", "")
        or _manifest_failure(manifest).get("classification", "")
    )
    reason = str(
        payload.get("reason", "")
        or _manifest_failure(manifest).get("reason", "")
        or ("required CI is still running" if outcome == "WAITING" else "AutoDev did not reach PR_READY")
    )

    completed = [] if detached_operation else _completed_work(manifest, state)
    blocker = _blocker_summary(outcome, failed_stage, classification, reason, state)
    next_steps = _next_steps(outcome, classification, payload, state)
    evidence = [] if detached_operation else _evidence_paths(current, payload, state)
    excerpt = (
        ""
        if detached_operation
        else _authoritative_excerpt(current, outcome, failed_stage)
    )

    lines = [
        "# AutoDev non-success report",
        "",
        "## Outcome",
        "",
        f"- State: `{redact(outcome)}`",
    ]
    if detached_operation:
        lines.extend(
            [
                f"- Requested operation: start issue `#{issue_number}`"
                if issue_number
                else "- Requested operation: start issue `(unknown)`",
                "- New run preparation completed: `no`",
                "- Branch for requested issue: `(none)`",
                "- Commit for requested issue: `(none)`",
                "- Exact PR head for requested issue: `(none)`",
                "- PR for requested issue: `(none)`",
            ]
        )
    else:
        lines.extend(
            [
                f"- Issue: `#{issue_number}`" if issue_number else "- Issue: `(unknown)`",
                f"- Branch: `{redact(branch)}`" if branch else "- Branch: `(unknown)`",
                f"- Commit: `{redact(commit)}`" if commit else "- Commit: `(none)`",
                f"- Exact PR head: `{redact(pr_head)}`" if pr_head else "- Exact PR head: `(none)`",
                f"- PR: {redact(pr_url)}" if pr_url else "- PR: `(none)`",
            ]
        )
    lines.extend(
        [
            f"- Stage: `{redact(failed_stage)}`" if failed_stage else "- Stage: `(unknown)`",
            f"- Classification: `{redact(classification)}`" if classification else "- Classification: `(none)`",
            "",
            "## What succeeded",
            "",
        ]
    )
    if completed:
        lines.extend(f"- {redact(item)}" for item in completed)
    elif detached_operation:
        lines.append(
            "- No durable state for the requested issue was created before the failure."
        )
    else:
        lines.append("- No durable completed stage could be confirmed from the current run artifacts.")

    if detached_operation:
        previous = payload.get("existing_durable_run", {})
        previous = previous if isinstance(previous, dict) else {}
        lines.extend(["", "## Existing durable run preserved", ""])
        if previous:
            prior_issue = int(previous.get("issue_number", 0) or 0)
            prior_branch = str(previous.get("branch", ""))
            prior_commit = str(previous.get("commit_sha", ""))
            prior_pr = str(previous.get("pr_url", ""))
            lines.extend(
                [
                    f"- Issue: `#{prior_issue}`" if prior_issue else "- Issue: `(unknown)`",
                    f"- Branch: `{redact(prior_branch)}`" if prior_branch else "- Branch: `(none)`",
                    f"- Commit: `{redact(prior_commit)}`" if prior_commit else "- Commit: `(none)`",
                    f"- PR: {redact(prior_pr)}" if prior_pr else "- PR: `(none)`",
                    "- This preserved run was not the failing operation.",
                ]
            )
        else:
            lines.append("- No prior durable run existed.")
        if issue_number:
            lines.append(
                f"- No issue #{issue_number} branch, commit, or PR was created."
            )

    lines.extend(
        [
            "",
            "## What prevented completion",
            "",
            blocker,
        ]
    )
    if excerpt:
        lines.extend(
            [
                "",
                "Authoritative evidence excerpt:",
                "",
                "```text",
                redact(excerpt),
                "```",
            ]
        )

    lines.extend(["", "## Next steps", ""])
    lines.extend(f"{index}. {redact(step)}" for index, step in enumerate(next_steps, start=1))
    lines.extend(["", "## Evidence", ""])
    if evidence:
        lines.extend(f"- `{redact(item)}`" for item in evidence)
    else:
        lines.append("- No additional evidence artifact was found.")

    fingerprint = str(payload.get("failure_fingerprint", ""))
    if fingerprint:
        lines.append(f"- Failure fingerprint: `{redact(fingerprint)}`")
    repair_counts = _repair_counts(diagnostics)
    if repair_counts:
        lines.append(f"- Repair attempts: {redact(repair_counts)}")

    if outcome == "WAITING":
        lines.extend(
            [
                "",
                "> This run is incomplete because external CI is still nonterminal. This does not mean the implementation failed.",
            ]
        )

    return "\n".join(lines).rstrip() + "\n"


def _load_manifest(repo: Path) -> dict[str, object]:
    path = run_manifest.manifest_path(repo / workflow_stages.CURRENT_DIR)
    if not path.is_file():
        return {}
    try:
        return run_manifest.load_manifest(path)
    except (OSError, ValueError, run_manifest.ManifestError):
        return {}


def _manifest_failure(manifest: dict[str, object]) -> dict[str, object]:
    value = manifest.get("failure", {})
    return value if isinstance(value, dict) else {}


def _manifest_current_stage(manifest: dict[str, object]) -> str:
    return str(manifest.get("current_stage", "")) if manifest else ""


def _completed_work(manifest: dict[str, object], state: dict[str, object]) -> list[str]:
    labels = {
        "issue-selected": "Issue selected and durable run state created.",
        "repository-read": "Repository reader completed.",
        "handoff-synthesized": "Repository evidence handoff synthesized.",
        "plan-created": "Implementation plan completed.",
        "implementation-generated": "Implementation role completed.",
        "patch-applied": "Implementation/repair patch checkpointed.",
        "deterministic-verified": "Deterministic local verification passed.",
        "semantic-verified": "Semantic verification passed.",
        "pr-created": "PR created and required CI reached terminal success.",
    }
    completed: list[str] = []
    raw = manifest.get("completed_stages", []) if manifest else []
    if isinstance(raw, list):
        for stage in raw:
            label = labels.get(str(stage))
            if label and label not in completed:
                completed.append(label)

    if bool(state.get("LastLocalCheckPassed")) and labels["deterministic-verified"] not in completed:
        completed.append(labels["deterministic-verified"])
    if str(state.get("LastSemanticVerdict", "")) == "pass" and labels["semantic-verified"] not in completed:
        completed.append(labels["semantic-verified"])
    if str(state.get("LastCommitSha", "")).strip():
        completed.append("A shipment commit was created.")
    if str(state.get("PrUrl", "")).strip():
        completed.append("A pull request exists for the run branch.")

    ci = state.get("CiProof", {})
    if isinstance(ci, dict):
        checks = ci.get("checks", [])
        if isinstance(checks, list):
            passed = [
                str(check.get("name", ""))
                for check in checks
                if isinstance(check, dict)
                and str(check.get("name", "")).strip()
                and str(check.get("bucket", "")).casefold() in {"pass", "skipping", "neutral"}
            ]
            if passed:
                completed.append("CI already successful/non-blocking: " + ", ".join(passed[:12]) + (" …" if len(passed) > 12 else ""))
    return completed


def _blocker_summary(
    outcome: str,
    failed_stage: str,
    classification: str,
    reason: str,
    state: dict[str, object],
) -> str:
    if outcome == "WAITING":
        ci = state.get("CiProof", {})
        polls = int(ci.get("polls", 0) or 0) if isinstance(ci, dict) else 0
        ci_state = str(ci.get("state", "")) if isinstance(ci, dict) else ""
        return (
            f"Required CI is still nonterminal (`{redact(ci_state or 'queued/in-progress')}`)"
            + (f" after {polls} polls" if polls else "")
            + ". No code failure has been established."
        )
    prefix = f"Stage `{redact(failed_stage)}` stopped the run. " if failed_stage else ""
    class_text = f"Classification: `{redact(classification)}`. " if classification else ""
    return prefix + class_text + redact(reason)


def _next_steps(
    outcome: str,
    classification: str,
    payload: dict[str, object],
    state: dict[str, object],
) -> list[str]:
    if (
        int(payload.get("requested_issue_number", 0) or 0) > 0
        and payload.get("new_run_prepared") is False
    ):
        requested = int(payload.get("requested_issue_number", 0) or 0)
        previous = payload.get("existing_durable_run", {})
        previous_issue = (
            int(previous.get("issue_number", 0) or 0)
            if isinstance(previous, dict)
            else 0
        )
        steps = [
            "Correct the reported preflight/preparation cause without deleting the preserved durable run.",
            f"Retry the requested operation with `autodev issue-to-pr {requested}`.",
        ]
        if previous_issue:
            steps.append(
                f"Use `autodev resume` only if you intentionally want to resume preserved issue #{previous_issue}; it does not resume the failed issue #{requested} request."
            )
        return steps

    if outcome == "WAITING":
        head = str(payload.get("pr_head_sha", "") or state.get("PrHeadSha", ""))
        return [
            f"Wait for the required checks on exact PR head `{head}` to reach a terminal state." if head else "Wait for the required PR checks to reach a terminal state.",
            "Run `autodev coordinate --resume`.",
            "If the same-head checks pass, AutoDev should advance to PR_READY; only a terminal unsuccessful check should enter the CI repair path.",
        ]

    repeated = bool(payload.get("repeated_failure")) or bool(payload.get("failure_fingerprint"))
    if repeated and classification == workflow_stages.FAILURE_DETERMINISTIC:
        return [
            "Do not retry the unchanged run; inspect the decisive evidence below and correct the deterministic cause first.",
            "After the source/configuration is corrected, run `autodev coordinate --resume` if the checkpoint remains valid; otherwise restart intentionally.",
        ]
    if classification == workflow_stages.FAILURE_TRANSIENT:
        return [
            "Correct or wait out the reported provider/GitHub/infrastructure condition.",
            "Run `autodev coordinate --resume`; completed durable stages should be reused.",
        ]
    if outcome == "BLOCKED":
        return [
            "Inspect the evidence/artifact paths below and make the required human correction or decision.",
            "Resume only after the blocker is actually resolved; do not consume more repair attempts by retrying unchanged.",
        ]
    return [
        "Inspect the decisive evidence/artifact paths below and correct the reported cause.",
        "Run `autodev coordinate --resume` when the run is again safely resumable.",
    ]


def _evidence_paths(current: Path, payload: dict[str, object], state: dict[str, object]) -> list[str]:
    candidates = [
        current / "state.json",
        current / run_manifest.MANIFEST_NAME,
        current / workflow_stages.DIAGNOSTICS_FILE,
        current / "local-check.log",
        current / "verification-result.json",
        current / "ci-summary.json",
        current / "opencode-last-failure.json",
    ]
    artifact = str(payload.get("artifact", ""))
    if artifact:
        artifact_path = Path(artifact)
        if not artifact_path.is_absolute():
            artifact_path = current / artifact_path.name
        candidates.append(artifact_path)
    existing: list[str] = []
    for path in candidates:
        if not path.is_file():
            continue
        try:
            relative = path.resolve().relative_to(current.parents[1].resolve()).as_posix()
        except ValueError:
            relative = str(path)
        if relative not in existing:
            existing.append(relative)
    pr_url = str(payload.get("pr_url", "") or state.get("PrUrl", ""))
    if pr_url:
        existing.append(pr_url)
    return existing


def _authoritative_excerpt(current: Path, outcome: str, failed_stage: str) -> str:
    if outcome == "WAITING" or "ci" in failed_stage.casefold() or "pr" in failed_stage.casefold():
        ci = workflow_stages.read_json(current / "ci-summary.json")
        if isinstance(ci, dict):
            checks = ci.get("checks", [])
            lines = [
                f"CI state: {ci.get('state', '')}",
                f"Head: {ci.get('head_sha', '')}",
                f"Polls: {ci.get('polls', 0)}",
            ]
            if isinstance(checks, list):
                for check in checks[:20]:
                    if isinstance(check, dict):
                        lines.append(
                            f"- {check.get('name', '(unnamed)')}: {check.get('state', '') or check.get('bucket', '')}"
                        )
            return "\n".join(lines)[:MAX_EVIDENCE_CHARS]

    candidates: list[Path]
    lowered = failed_stage.casefold()
    if "local" in lowered or "deterministic" in lowered:
        candidates = [current / "local-check.log"]
    elif "semantic" in lowered or "verifier" in lowered:
        candidates = [current / "verification-result.json"]
    else:
        candidates = [current / "opencode-last-failure.json", current / "local-check.log"]
    for path in candidates:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if text.strip():
            return text[-MAX_EVIDENCE_CHARS:]
    return ""


def _repair_counts(diagnostics: dict[str, object]) -> str:
    if not diagnostics:
        return ""
    values: list[str] = []
    for key in ("local_repair_attempt", "semantic_repair_attempt", "ci_repair_attempt"):
        if key in diagnostics:
            values.append(f"{key}={diagnostics.get(key)}")
    role_invocations = diagnostics.get("role_invocations", {})
    if isinstance(role_invocations, dict) and role_invocations:
        values.append("role_invocations=" + json.dumps(role_invocations, sort_keys=True))
    return ", ".join(values)


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)
