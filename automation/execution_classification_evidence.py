from __future__ import annotations

from automation import opencode_adapter_protocol

import subprocess
from dataclasses import replace
from pathlib import Path

from automation import (
    execution_classification as execution,
    execution_classification_hooks as hooks,
    issue_queue,
    role_coordinator_flow,
    role_resume,
    role_runtime,
    workflow_stages,
)


def _fresh_issue_text(issue_number: int, issue: dict[str, object]) -> str:
    title = str(issue.get("title", "")).strip()
    url = str(issue.get("url", "")).strip()
    body = str(issue.get("body", "") or "")
    return f"# GitHub Issue #{issue_number}: {title}\n\nURL: {url}\n\n{body}\n"


def refresh_manual_completion_evidence(
    repo: Path,
    *,
    runner,
) -> execution.ExecutionReport | None:
    """Refresh only the explicit, secret-free manual completion signal.

    AutoDev intentionally does not scrape prose/comments looking for implied
    completion. The operator must add the documented marker to the issue body.
    The refreshed body is then handed back to Reader for a bounded semantic
    reclassification of whatever work remains.
    """

    repo = repo.expanduser().resolve()
    current = repo / workflow_stages.CURRENT_DIR
    report = execution.load_report(current)
    if report is None or not report.attention_required:
        return report

    state = workflow_stages.read_state(current)
    repo_full = str(state.get("RepoFullName", "")).strip()
    issue_number = int(state.get("IssueNumber", 0) or 0)
    if not repo_full or issue_number <= 0:
        return report

    issue = workflow_stages.gh_json(
        repo,
        [
            "issue",
            "view",
            str(issue_number),
            "--repo",
            repo_full,
            "--json",
            "number,title,body,url,labels",
        ],
        runner=runner,
    )
    issue_text = _fresh_issue_text(issue_number, issue)
    if not execution.manual_evidence_present(issue_text):
        return report

    labels = [
        str(item.get("name", ""))
        for item in issue.get("labels", [])
        if isinstance(item, dict) and str(item.get("name", "")).strip()
    ]
    workflow_stages.write_text(current / "issue.md", issue_text)
    state["IssueText"] = issue_text
    state["Labels"] = labels
    refreshed = replace(
        report,
        completion_evidence_present=True,
        source=f"{report.source}-manual-evidence",
    )
    execution.apply_state_fields(state, refreshed)
    state["Status"] = "ManualEvidenceAccepted"
    state["QueueState"] = "running"
    workflow_stages.write_state(current, state)
    execution.persist_artifacts(current, refreshed)

    issue_queue.ensure_queue_labels(repo, repo_full, runner=runner)
    workflow_stages.gh(
        repo,
        [
            "issue",
            "edit",
            str(issue_number),
            "--repo",
            repo_full,
            "--remove-label",
            issue_queue.ATTENTION_LABEL,
            "--remove-label",
            issue_queue.READY_LABEL,
            "--remove-label",
            issue_queue.BLOCKED_LABEL,
            "--add-label",
            issue_queue.RUNNING_LABEL,
        ],
        runner=runner,
    )
    return refreshed


def _install_fail_closed_queue_evidence_cache() -> None:
    # The queue hook keeps body-derived evidence separate from QueueIssue so the
    # existing queue API remains stable. Clear that ephemeral cache before every
    # authoritative refresh: if the extra body query fails, absence of fresh
    # evidence must keep `autodev:attention` rather than reusing a stale `true`.
    current_list = issue_queue.list_issues
    if not getattr(current_list, "_autodev_manual_evidence_cache_reset", False):
        original_list = current_list

        def list_issues(
            repo: Path,
            github_repo: str,
            *,
            limit: int = issue_queue.DEFAULT_LIMIT,
            runner=subprocess.run,
        ):
            hooks._MANUAL_EVIDENCE_BY_ISSUE.clear()  # type: ignore[attr-defined]
            return original_list(
                repo,
                github_repo,
                limit=limit,
                runner=runner,
            )

        list_issues._autodev_manual_evidence_cache_reset = True  # type: ignore[attr-defined]
        issue_queue.list_issues = list_issues

    current_fetch = issue_queue.fetch_issue
    if not getattr(current_fetch, "_autodev_manual_evidence_cache_reset", False):
        original_fetch = current_fetch

        def fetch_issue(
            repo: Path,
            github_repo: str,
            issue_number: int,
            *,
            runner=subprocess.run,
        ):
            hooks._MANUAL_EVIDENCE_BY_ISSUE.pop(  # type: ignore[attr-defined]
                issue_number,
                None,
            )
            return original_fetch(
                repo,
                github_repo,
                issue_number,
                runner=runner,
            )

        fetch_issue._autodev_manual_evidence_cache_reset = True  # type: ignore[attr-defined]
        issue_queue.fetch_issue = fetch_issue


def _install_attention_prepare_manifest() -> None:
    # Normal prepare creates the resumability manifest only for CONTINUE. An
    # explicit manual declaration intentionally returns ATTENTION_REQUIRED from
    # prepare, but that state must still be resumable after the human supplies
    # the completion marker. Create the same issue-selected checkpoint without
    # launching any role or creating any shipment branch/PR.
    current = role_coordinator_flow.run_stage
    if getattr(current, "_autodev_manual_attention_manifest", False):
        return
    original = current

    def run_stage(repo: Path, name: str, **kwargs):
        payload = original(repo, name, **kwargs)
        resolved = Path(repo).expanduser().resolve()
        current_dir = resolved / workflow_stages.CURRENT_DIR
        if (
            name == "prepare"
            and payload.get("state") == hooks.ATTENTION_STATE
            and current_dir.is_dir()
            and not role_resume.has_manifest(resolved)
        ):
            runtime_name = str(kwargs.get("runtime_name", "")).strip() or "opencode"
            opencode_adapter_protocol._ensure_opencode_protocol(current_dir)
            role_resume.create_manifest(
                resolved,
                workflow_stages.read_state(current_dir),
                runtime_name=runtime_name,
            )
            role_runtime.persist_selection(
                resolved,
                name=runtime_name,
                source="selected",
                force_manifest=True,
            )
        return payload

    run_stage._autodev_manual_attention_manifest = True  # type: ignore[attr-defined]
    role_coordinator_flow.run_stage = run_stage


def install() -> None:
    _install_fail_closed_queue_evidence_cache()
    _install_attention_prepare_manifest()

    current = hooks._attention_resume_payload  # type: ignore[attr-defined]
    if getattr(current, "_autodev_manual_evidence_refresh", False):
        return
    original = current

    def _attention_resume_payload(repo: Path, base=None, *, runner):
        resolved = Path(repo).expanduser().resolve()
        report = refresh_manual_completion_evidence(resolved, runner=runner)
        if report is not None and report.completion_evidence_present:
            payload = dict(base or {})
            # Reader must see the refreshed issue and decide whether the remaining
            # work is now automatable. Do not continue from a stale manual Reader
            # brief merely because the external prerequisite was completed.
            payload.update(
                {
                    "state": "RESUME",
                    "next_action": "reader",
                    "next_role": "reader",
                    "next_stage": "repository-read",
                    "execution_classification": report.classification,
                    "manual_completion_evidence_present": True,
                    "successful_non_runnable": False,
                }
            )
            return payload
        return original(resolved, base, runner=runner)

    _attention_resume_payload._autodev_manual_evidence_refresh = True  # type: ignore[attr-defined]
    hooks._attention_resume_payload = _attention_resume_payload  # type: ignore[attr-defined]
