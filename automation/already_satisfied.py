from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Callable

from automation import (
    repair_lineage,
    run_manifest,
    semantic_prompts,
    semantic_schema,
    ux_multimodal,
    ux_multimodal_runtime,
    workflow_github,
    workflow_stages,
    workflow_verification,
)
from automation.workflow_contract import AUTODEV_ROOT
from automation.workflow_diagnostics import stage_payload
from automation.workflow_storage import read_state, read_text, write_state, write_text
from automation.workflow_workspace import source_identity


CANDIDATE_MARKER = "AUTODEV_ALREADY_SATISFIED_CANDIDATE: yes"
RECORD_KEY = "already_satisfied"
REPORT_NAME = "already-satisfied-report.md"


class AlreadySatisfiedError(RuntimeError):
    pass


def _current(repo: Path) -> Path:
    return repo.expanduser().resolve() / workflow_stages.CURRENT_DIR


def _manifest_path(repo: Path) -> Path:
    return _current(repo) / run_manifest.MANIFEST_NAME


def candidate_in_plan(repo: Path) -> bool:
    plan = _current(repo) / "plan.md"
    if not plan.is_file():
        return False
    try:
        lines = plan.read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    return any(line.strip() == CANDIDATE_MARKER for line in lines)


def _context(repo: Path, manifest: dict[str, object]) -> dict[str, str]:
    current = _current(repo)
    target = manifest.get("target", {})
    target = target if isinstance(target, dict) else {}
    roles = manifest.get("roles", {})
    roles = roles if isinstance(roles, dict) else {}
    verifier = roles.get("verifier", {})
    verifier = verifier if isinstance(verifier, dict) else {}
    return {
        "prepared_base_sha": str(target.get("base_sha", "")),
        "issue_sha256": run_manifest.hash_file(current / "issue.md"),
        "plan_sha256": run_manifest.hash_file(current / "plan.md"),
        "verifier_fingerprint": str(verifier.get("fingerprint", "")),
    }


def _record_matches(record: dict[str, object], context: dict[str, str]) -> bool:
    return all(str(record.get(key, "")) == value for key, value in context.items())


def ensure_candidate_record(repo: Path) -> dict[str, object]:
    repo = repo.expanduser().resolve()
    if not candidate_in_plan(repo):
        return {}
    path = _manifest_path(repo)
    manifest = run_manifest.load_manifest(path)
    context = _context(repo, manifest)
    existing = manifest.get(RECORD_KEY, {})
    if isinstance(existing, dict) and _record_matches(existing, context):
        return dict(existing)
    record: dict[str, object] = {
        "status": "candidate",
        "candidate_marker": CANDIDATE_MARKER,
        "created_at": run_manifest.utc_now(),
        **context,
        "deterministic": {},
        "semantic": {},
    }
    manifest[RECORD_KEY] = record
    run_manifest.save_manifest(path, manifest)
    return dict(record)


def _save_record(repo: Path, record: dict[str, object]) -> dict[str, object]:
    path = _manifest_path(repo)
    manifest = run_manifest.load_manifest(path)
    manifest[RECORD_KEY] = dict(record)
    run_manifest.save_manifest(path, manifest)
    return dict(record)


def record_is_confirmed(
    manifest: dict[str, object],
    state: dict[str, object],
) -> bool:
    record = manifest.get(RECORD_KEY, {})
    if not isinstance(record, dict) or str(record.get("status", "")) != "confirmed":
        return False
    roles = manifest.get("roles", {})
    roles = roles if isinstance(roles, dict) else {}
    verifier = roles.get("verifier", {})
    verifier = verifier if isinstance(verifier, dict) else {}
    return (
        str(record.get("prepared_base_sha", "")) == str(state.get("BaseSha", ""))
        and str(record.get("issue_sha256", ""))
        == str(state.get("AlreadySatisfiedIssueSha256", ""))
        and str(record.get("plan_sha256", ""))
        == str(state.get("AlreadySatisfiedPlanSha256", ""))
        and str(record.get("verifier_fingerprint", ""))
        == str(verifier.get("fingerprint", ""))
    )


def current_is_confirmed(repo: Path) -> bool:
    path = _manifest_path(repo)
    if not path.is_file():
        return False
    try:
        manifest = run_manifest.load_manifest(path)
        state = read_state(_current(repo))
    except (OSError, ValueError, run_manifest.ManifestError, workflow_stages.WorkflowStageError):
        return False
    return record_is_confirmed(manifest, state)


def role_prompt_context(repo: Path, role: str) -> str:
    if role != "verifier" or not candidate_in_plan(repo):
        return ""
    try:
        record = ensure_candidate_record(repo)
    except (OSError, ValueError, run_manifest.ManifestError):
        return ""
    if str(record.get("status", "")) != "deterministic-pass":
        return ""
    return (
        "\n\nAlready-satisfied verification mode:\n"
        "The Planner marked the prepared base as a candidate for already-satisfied completion. "
        "No implementation patch has been applied. Independently verify the issue acceptance criteria "
        "against the current prepared-base repository state and deterministic evidence. An empty diff is "
        "expected here and is not evidence of failure by itself. Return pass only when the prepared base "
        "itself satisfies every required criterion; otherwise return repair or blocked as appropriate.\n"
    )


def _clean_source_proof(repo: Path, state: dict[str, object]) -> tuple[bool, dict[str, object], str]:
    proof = source_identity(repo, _current(repo), state)
    base = str(state.get("BaseSha", ""))
    changes = proof.get("changes", [])
    if str(proof.get("parent_sha", "")) != base:
        return False, proof, "source parent no longer matches the prepared base"
    if not isinstance(changes, list) or changes:
        return False, proof, "repository has changes relative to the prepared base"
    return True, proof, ""


def _reset_probe_verification_state(repo: Path) -> None:
    current = _current(repo)
    state = read_state(current)
    state["Status"] = "Planned"
    state["LastLocalCheckPassed"] = False
    for key in (
        "LocalCheckFailureClassification",
        "LocalCheckFailureReason",
        "VerifiedParentSha",
        "VerifiedSourceIdentity",
        "VerifiedChanges",
        "LastSemanticVerdict",
        "SemanticSourceIdentity",
        "AlreadySatisfiedIssueSha256",
        "AlreadySatisfiedPlanSha256",
        "AlreadySatisfiedBaseSha",
    ):
        state.pop(key, None)
    repair_lineage.clear_current_local_failure(state)
    write_state(current, state)


def _reject(repo: Path, record: dict[str, object], reason: str) -> str:
    record = dict(record)
    record["status"] = "rejected"
    record["rejected_at"] = run_manifest.utc_now()
    record["reason"] = reason
    _save_record(repo, record)
    _reset_probe_verification_state(repo)
    return "rejected"


def run_deterministic_probe(
    repo: Path,
    *,
    runner: Callable[..., object] = subprocess.run,
) -> str:
    repo = repo.expanduser().resolve()
    record = ensure_candidate_record(repo)
    if not record:
        return "not-candidate"
    status = str(record.get("status", ""))
    if status in {"confirmed", "rejected", "deterministic-pass"}:
        return status

    current = _current(repo)
    state = read_state(current)
    clean, proof, reason = _clean_source_proof(repo, state)
    if not clean:
        record["deterministic"] = {
            "passed": False,
            "source_identity": str(proof.get("identity", "")),
            "changes": proof.get("changes", []),
        }
        return _reject(repo, record, reason)

    try:
        passed = workflow_verification.run_local_check(
            repo,
            current,
            state,
            AUTODEV_ROOT,
            runner=runner,
        )
    except workflow_stages.WorkflowStageError as exc:
        record["deterministic"] = {
            "passed": False,
            "classification": str(getattr(exc, "classification", "") or "setup/configuration"),
            "reason": str(exc),
        }
        return _reject(repo, record, f"deterministic verification could not confirm the prepared base: {exc}")

    state = read_state(current)
    clean, proof, reason = _clean_source_proof(repo, state)
    log_path = current / "local-check.log"
    record["deterministic"] = {
        "passed": bool(passed and clean),
        "local_check": str(state.get("LocalCheck", "")),
        "log_sha256": run_manifest.hash_file(log_path) if log_path.is_file() else "",
        "source_identity": str(proof.get("identity", "")),
        "parent_sha": str(proof.get("parent_sha", "")),
        "changes": proof.get("changes", []),
    }
    if not passed:
        return _reject(repo, record, "deterministic verification failed on the prepared base")
    if not clean:
        return _reject(repo, record, reason)

    record["status"] = "deterministic-pass"
    record["deterministic_verified_at"] = run_manifest.utc_now()
    _save_record(repo, record)
    return "deterministic-pass"


def _requirements_summary(result: dict[str, object]) -> list[dict[str, object]]:
    raw = result.get("requirements", [])
    if not isinstance(raw, list):
        return []
    return [
        {
            "criterion": str(item.get("criterion", "")),
            "status": str(item.get("status", "")),
            "evidence": [str(value) for value in item.get("evidence", [])]
            if isinstance(item.get("evidence", []), list)
            else [],
        }
        for item in raw
        if isinstance(item, dict)
    ]


def _mark_issue_already_satisfied(
    current: Path,
    state: dict[str, object],
    *,
    runner: Callable[..., object],
) -> None:
    issue_number = int(state.get("IssueNumber", 0) or 0)
    repo_full = str(state.get("RepoFullName", ""))
    repo = current.parents[1]
    if issue_number and repo_full:
        workflow_github.gh(
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
        workflow_github.gh(
            repo,
            [
                "issue",
                "comment",
                str(issue_number),
                "--repo",
                repo_full,
                "--body",
                (
                    "AutoDev verified that the prepared base already satisfies this issue.\n\n"
                    f"Prepared base: `{state.get('BaseSha', '')}`\n\n"
                    "Deterministic verification: passed.\n"
                    "Semantic verification: passed.\n"
                    "No implementation commit or pull request was created."
                ),
            ],
            runner=runner,
        )
    state["Status"] = "AlreadySatisfied"
    write_state(current, state)


def finalize_semantic_probe(
    repo: Path,
    *,
    runner: Callable[..., object] = subprocess.run,
    which=None,
) -> str:
    repo = repo.expanduser().resolve()
    record = ensure_candidate_record(repo)
    if not record:
        return "not-candidate"
    if str(record.get("status", "")) == "confirmed":
        return "confirmed"
    if str(record.get("status", "")) != "deterministic-pass":
        return str(record.get("status", "")) or "rejected"

    current = _current(repo)
    state = read_state(current)
    result_path = current / "verification-result.json"
    if not result_path.is_file():
        raise AlreadySatisfiedError(
            "already-satisfied semantic probe has no accepted verification-result.json"
        )
    issue_text = read_text(current / "issue.md") or str(state.get("IssueText", ""))
    result = semantic_schema.parse_semantic_output(
        read_text(result_path),
        expected_criteria=semantic_prompts.extract_acceptance_criteria(issue_text) or None,
        current=current,
        role="verifier",
    )
    record["semantic"] = {
        "verdict": str(result.get("verdict", "")),
        "result_sha256": run_manifest.hash_file(result_path),
        "requirements": _requirements_summary(result),
    }
    if str(result.get("verdict", "")) != "pass":
        _save_record(repo, record)
        return _reject(
            repo,
            record,
            "semantic verifier found that the prepared base does not fully satisfy the issue",
        )

    try:
        multimodal_path = ux_multimodal_runtime.verify_for_semantic_stage(
            repo,
            runner=runner,
            which=which or shutil.which,
        )
        multimodal = ux_multimodal.load_result(current)
    except Exception as exc:
        semantic = record.get("semantic", {})
        if isinstance(semantic, dict):
            semantic["multimodal_status"] = "unverifiable"
            semantic["multimodal_reason"] = str(exc)
        _save_record(repo, record)
        return _reject(
            repo,
            record,
            f"semantic verification could not confirm required UX evidence on the prepared base: {exc}",
        )
    multimodal_status = str(multimodal.get("status", "") or "")
    semantic = record.get("semantic", {})
    if isinstance(semantic, dict):
        semantic["multimodal_status"] = multimodal_status
        semantic["multimodal_artifact"] = str(multimodal_path)
    if multimodal_status not in {"pass", "not-applicable"}:
        _save_record(repo, record)
        return _reject(
            repo,
            record,
            f"multimodal semantic verification did not pass on the prepared base: {multimodal_status or 'unknown'}",
        )

    state = read_state(current)
    clean, proof, reason = _clean_source_proof(repo, state)
    if not clean:
        _save_record(repo, record)
        return _reject(repo, record, reason)

    report = _render_report(record, state, proof)
    report_path = current / REPORT_NAME
    write_text(report_path, report)
    _mark_issue_already_satisfied(current, state, runner=runner)

    state = read_state(current)
    state["AlreadySatisfiedBaseSha"] = str(record.get("prepared_base_sha", ""))
    state["AlreadySatisfiedIssueSha256"] = str(record.get("issue_sha256", ""))
    state["AlreadySatisfiedPlanSha256"] = str(record.get("plan_sha256", ""))
    state["LastSemanticVerdict"] = "pass"
    state["SemanticSourceIdentity"] = str(proof.get("identity", ""))
    write_state(current, state)

    record["status"] = "confirmed"
    record["confirmed_at"] = run_manifest.utc_now()
    record["report"] = REPORT_NAME
    record["repository_modified"] = False
    record["commit_exists"] = False
    record["pr_exists"] = False
    _save_record(repo, record)
    return "confirmed"


def probe_candidate(
    repo: Path,
    *,
    semantic_verifier: Callable[[], object],
    runner: Callable[..., object] = subprocess.run,
    which=None,
) -> str:
    deterministic = run_deterministic_probe(repo, runner=runner)
    if deterministic != "deterministic-pass":
        return deterministic
    semantic_verifier()
    return finalize_semantic_probe(repo, runner=runner, which=which)


def _render_report(
    record: dict[str, object],
    state: dict[str, object],
    proof: dict[str, object],
) -> str:
    semantic = record.get("semantic", {})
    semantic = semantic if isinstance(semantic, dict) else {}
    requirements = semantic.get("requirements", [])
    requirements = requirements if isinstance(requirements, list) else []
    lines = [
        "# AutoDev already-satisfied report",
        "",
        "## Outcome",
        "",
        "- Outcome: already satisfied on prepared base",
        f"- Prepared base SHA: `{record.get('prepared_base_sha', '')}`",
        "- Repository modified: no",
        "- Commit: none",
        "- PR: none",
        "",
        "## Verification",
        "",
        "- Planner: candidate only; not authoritative",
        "- Deterministic verification: passed",
        "- Semantic verification: passed",
        f"- Multimodal UX verification: {semantic.get('multimodal_status', 'not-applicable')}",
        f"- Verified source identity: `{proof.get('identity', '')}`",
        "",
        "## Acceptance evidence",
        "",
    ]
    if requirements:
        for item in requirements:
            if not isinstance(item, dict):
                continue
            criterion = str(item.get("criterion", ""))
            status = str(item.get("status", ""))
            evidence = item.get("evidence", [])
            evidence_text = "; ".join(str(value) for value in evidence) if isinstance(evidence, list) else ""
            lines.append(f"- [{status}] {criterion}" + (f" — {evidence_text}" if evidence_text else ""))
    else:
        lines.append("- Verifier returned a clean pass with no separately enumerated criteria.")
    lines.extend(
        [
            "",
            "No implementation, cosmetic cleanup, commit, branch shipment, or pull request was required.",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def terminal_payload(repo: Path) -> dict[str, object]:
    repo = repo.expanduser().resolve()
    current = _current(repo)
    state = read_state(current)
    manifest = run_manifest.load_manifest(_manifest_path(repo))
    record = manifest.get(RECORD_KEY, {})
    record = record if isinstance(record, dict) else {}
    result = stage_payload(
        repo,
        "ALREADY_SATISFIED",
        "already-satisfied",
        reason="prepared base already satisfies the issue; no implementation or shipment required",
        artifact=current / REPORT_NAME,
        next_action="none",
    )
    result["outcome"] = "already-satisfied"
    result["prepared_base_sha"] = str(record.get("prepared_base_sha", state.get("BaseSha", "")))
    result["branch"] = ""
    result["repository_modified"] = False
    result["commit_exists"] = False
    result["pr_exists"] = False
    result["pr_url"] = ""
    result["verification_performed"] = ["deterministic", "semantic"]
    result["acceptance_evidence"] = str(current / REPORT_NAME)
    return result
