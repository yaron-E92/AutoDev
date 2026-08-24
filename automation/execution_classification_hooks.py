from __future__ import annotations

from automation import queue_classification, queue_contract, queue_github

from automation import opencode_resume_status

from automation import opencode_resume_execution

from automation import opencode_adapter_roles

from automation import opencode_adapter_handoff

from automation import opencode_adapter_contract

import json
import subprocess
from pathlib import Path

from automation import execution_classification as execution, role_coordinator_flow, role_resume, workflow_stages


ATTENTION_STATE = "ATTENTION_REQUIRED"
ATTENTION_STAGE = "execution-classification"
ATTENTION_COMMENT_MARKER = "<!-- autodev:manual-attention -->"

# The queue data class intentionally remains small. This cache is populated only
# from deterministic issue metadata fetched by the queue's own list/view calls.
_MANUAL_EVIDENCE_BY_ISSUE: dict[int, bool] = {}


class _AttentionRequired(RuntimeError):
    def __init__(self, payload: dict[str, object]) -> None:
        super().__init__(str(payload.get("reason", "manual/external action is required")))
        self.payload = dict(payload)


def _canonical_label_specs() -> None:
    queue_contract.LABEL_SPECS.update(
        {
            queue_contract.MANAGED_LABEL: (
                "1d76db",
                "Human authorization for autonomous AutoDev work",
            ),
            queue_contract.READY_LABEL: (
                "0e8a16",
                "Derived: managed and currently runnable by AutoDev",
            ),
            queue_contract.BLOCKED_LABEL: (
                "d93f0b",
                "Derived: managed but blocked by open issue dependencies",
            ),
            queue_contract.ATTENTION_LABEL: (
                "fbca04",
                "Human attention is required before autonomous AutoDev work",
            ),
            queue_contract.RUNNING_LABEL: (
                "5319e7",
                "Active AutoDev claim/run for this issue",
            ),
        }
    )


def _install_queue_label_bootstrap() -> None:
    _canonical_label_specs()
    current = queue_github.ensure_queue_labels
    if getattr(current, "_autodev_execution_classification", False):
        return

    def ensure_queue_labels(
        repo: Path,
        github_repo: str,
        *,
        runner=subprocess.run,
    ) -> tuple[str, ...]:
        result = queue_github._run_gh(  # type: ignore[attr-defined]
            repo,
            [
                "label",
                "list",
                "--repo",
                github_repo,
                "--limit",
                "1000",
                "--json",
                "name,color,description",
            ],
            runner=runner,
        )
        raw = queue_github._json_result(  # type: ignore[attr-defined]
            result,
            context="gh label list",
        )
        if not isinstance(raw, list):
            raise queue_contract.QueueError("gh label list did not return an array")
        existing: dict[str, tuple[str, str]] = {}
        for item in raw:
            if not isinstance(item, dict) or not item.get("name"):
                continue
            existing[str(item["name"])] = (
                str(item.get("color", "")).casefold(),
                str(item.get("description", "")),
            )

        created: list[str] = []
        for name, (color, description) in queue_contract.LABEL_SPECS.items():
            actual = existing.get(name)
            if actual is None:
                queue_github._run_gh(  # type: ignore[attr-defined]
                    repo,
                    [
                        "label",
                        "create",
                        name,
                        "--repo",
                        github_repo,
                        "--color",
                        color,
                        "--description",
                        description,
                    ],
                    runner=runner,
                )
                created.append(name)
                continue
            if actual != (color.casefold(), description):
                queue_github._run_gh(  # type: ignore[attr-defined]
                    repo,
                    [
                        "label",
                        "edit",
                        name,
                        "--repo",
                        github_repo,
                        "--color",
                        color,
                        "--description",
                        description,
                    ],
                    runner=runner,
                )
        return tuple(created)

    ensure_queue_labels._autodev_execution_classification = True  # type: ignore[attr-defined]
    queue_github.ensure_queue_labels = ensure_queue_labels


def _cache_issue_evidence(raw: object) -> None:
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        return
    for item in raw:
        if not isinstance(item, dict) or not item.get("number"):
            continue
        _MANUAL_EVIDENCE_BY_ISSUE[int(item["number"])] = execution.manual_evidence_present(
            str(item.get("body", ""))
        )


def _install_queue_evidence_reconciliation() -> None:
    current_list = queue_github.list_issues
    if not getattr(current_list, "_autodev_execution_classification", False):
        original_list = current_list

        def list_issues(
            repo: Path,
            github_repo: str,
            *,
            limit: int = queue_contract.DEFAULT_LIMIT,
            runner=subprocess.run,
        ):
            issues = original_list(repo, github_repo, limit=limit, runner=runner)
            result = queue_github._run_gh(  # type: ignore[attr-defined]
                repo,
                [
                    "issue",
                    "list",
                    "--repo",
                    github_repo,
                    "--state",
                    "all",
                    "--limit",
                    str(limit),
                    "--json",
                    "number,body",
                ],
                runner=runner,
                check=False,
            )
            if result.returncode == 0:
                try:
                    _cache_issue_evidence(
                        queue_github._json_result(  # type: ignore[attr-defined]
                            result,
                            context="gh issue list evidence",
                        )
                    )
                except queue_contract.QueueError:
                    pass
            return issues

        list_issues._autodev_execution_classification = True  # type: ignore[attr-defined]
        queue_github.list_issues = list_issues

    current_fetch = queue_github.fetch_issue
    if not getattr(current_fetch, "_autodev_execution_classification", False):
        original_fetch = current_fetch

        def fetch_issue(
            repo: Path,
            github_repo: str,
            issue_number: int,
            *,
            runner=subprocess.run,
        ):
            issue = original_fetch(repo, github_repo, issue_number, runner=runner)
            result = queue_github._run_gh(  # type: ignore[attr-defined]
                repo,
                [
                    "issue",
                    "view",
                    str(issue_number),
                    "--repo",
                    github_repo,
                    "--json",
                    "number,body",
                ],
                runner=runner,
                check=False,
            )
            if result.returncode == 0:
                try:
                    _cache_issue_evidence(
                        queue_github._json_result(  # type: ignore[attr-defined]
                            result,
                            context="gh issue view evidence",
                        )
                    )
                except queue_contract.QueueError:
                    pass
            return issue

        fetch_issue._autodev_execution_classification = True  # type: ignore[attr-defined]
        queue_github.fetch_issue = fetch_issue

    current_classify = queue_classification.classify_issue
    if not getattr(current_classify, "_autodev_execution_classification", False):
        original_classify = current_classify

        def classify_issue(issue, blockers, policy):
            state = original_classify(issue, blockers, policy)
            labels = set(issue.labels)
            if (
                state.reason == "attention"
                and queue_contract.MANAGED_LABEL in labels
                and _MANUAL_EVIDENCE_BY_ISSUE.get(issue.number, False)
            ):
                adjusted = queue_contract.QueueIssue(
                    number=issue.number,
                    title=issue.title,
                    url=issue.url,
                    state=issue.state,
                    labels=tuple(
                        label
                        for label in issue.labels
                        if label != queue_contract.ATTENTION_LABEL
                    ),
                )
                recalculated = original_classify(adjusted, blockers, policy)
                return queue_contract.QueueState(
                    issue=issue,
                    reason=recalculated.reason,
                    open_blockers=recalculated.open_blockers,
                    closed_blockers=recalculated.closed_blockers,
                )
            return state

        classify_issue._autodev_execution_classification = True  # type: ignore[attr-defined]
        queue_classification.classify_issue = classify_issue

    current_update = queue_classification._update_derived_labels  # type: ignore[attr-defined]
    if not getattr(current_update, "_autodev_execution_classification", False):
        original_update = current_update

        def _update_derived_labels(
            repo: Path,
            github_repo: str,
            state,
            *,
            runner=subprocess.run,
        ) -> bool:
            labels = set(state.issue.labels)
            evidence_clears_attention = (
                queue_contract.MANAGED_LABEL in labels
                and queue_contract.ATTENTION_LABEL in labels
                and _MANUAL_EVIDENCE_BY_ISSUE.get(state.issue.number, False)
            )
            effective_state = state
            if evidence_clears_attention:
                adjusted_issue = queue_contract.QueueIssue(
                    number=state.issue.number,
                    title=state.issue.title,
                    url=state.issue.url,
                    state=state.issue.state,
                    labels=tuple(
                        label
                        for label in state.issue.labels
                        if label != queue_contract.ATTENTION_LABEL
                    ),
                )
                effective_state = queue_contract.QueueState(
                    issue=adjusted_issue,
                    reason=state.reason,
                    open_blockers=state.open_blockers,
                    closed_blockers=state.closed_blockers,
                )
            changed = original_update(
                repo,
                github_repo,
                effective_state,
                runner=runner,
            )
            if evidence_clears_attention:
                queue_github._run_gh(  # type: ignore[attr-defined]
                    repo,
                    [
                        "issue",
                        "edit",
                        str(state.issue.number),
                        "--repo",
                        github_repo,
                        "--remove-label",
                        queue_contract.ATTENTION_LABEL,
                    ],
                    runner=runner,
                )
                changed = True
            return changed

        _update_derived_labels._autodev_execution_classification = True  # type: ignore[attr-defined]
        queue_classification._update_derived_labels = _update_derived_labels  # type: ignore[attr-defined]


def _attention_payload(
    repo: Path,
    current: Path,
    report: execution.ExecutionReport,
) -> dict[str, object]:
    state = workflow_stages.read_state(current)
    artifact = current / execution.MANUAL_ACTION_PLAN_FILE
    payload = workflow_stages.stage_payload(
        repo,
        ATTENTION_STATE,
        ATTENTION_STAGE,
        reason=report.reason,
        artifact=artifact if artifact.is_file() else current / execution.CLASSIFICATION_FILE,
        requested_issue=int(state.get("IssueNumber", 0) or 0),
        next_action="complete the secret-free manual/external actions and resume only when the declared evidence exists",
    )
    payload.update(
        {
            "execution_classification": report.classification,
            "execution_classification_source": report.source,
            "autonomous_criteria": list(report.autonomous_criteria),
            "manual_criteria": list(report.manual_criteria),
            "human_actions": list(report.human_actions),
            "resume_evidence": list(report.resume_evidence),
            "decomposition_recommended": report.decomposition_recommended,
            "queue_state": "attention",
            "successful_non_runnable": True,
        }
    )
    return payload


def _transition_attention(
    repo: Path,
    current: Path,
    report: execution.ExecutionReport,
    *,
    runner=subprocess.run,
) -> dict[str, object]:
    state = workflow_stages.read_state(current)
    repo_full = str(state.get("RepoFullName", "")).strip()
    issue_number = int(state.get("IssueNumber", 0) or 0)
    if repo_full and issue_number:
        queue_github.ensure_queue_labels(repo, repo_full, runner=runner)
        workflow_stages.gh(
            repo,
            [
                "issue",
                "edit",
                str(issue_number),
                "--repo",
                repo_full,
                "--remove-label",
                queue_contract.RUNNING_LABEL,
                "--remove-label",
                queue_contract.READY_LABEL,
                "--remove-label",
                queue_contract.BLOCKED_LABEL,
                "--add-label",
                queue_contract.ATTENTION_LABEL,
            ],
            runner=runner,
        )
        notification_key = f"{report.classification}:{report.reason}"
        if str(state.get("ManualAttentionNotificationKey", "")) != notification_key:
            plan = execution.render_manual_action_plan(report).strip()
            workflow_stages.gh(
                repo,
                [
                    "issue",
                    "comment",
                    str(issue_number),
                    "--repo",
                    repo_full,
                    "--body",
                    ATTENTION_COMMENT_MARKER
                    + "\nAutoDev classified this issue as requiring manual/external attention before autonomous implementation can continue.\n\n"
                    + plan,
                ],
                runner=runner,
            )
            state["ManualAttentionNotificationKey"] = notification_key

    execution.apply_state_fields(state, report)
    state["Status"] = "AttentionRequired"
    state["QueueState"] = "attention"
    workflow_stages.write_state(current, state)
    execution.persist_artifacts(current, report)
    return _attention_payload(repo, current, report)


def _install_prepare_gate() -> None:
    current = workflow_stages.execute_stage
    if getattr(current, "_autodev_execution_classification", False):
        return
    original = current

    def execute_stage(
        name: str,
        repo: Path,
        *,
        arguments: str = "",
        autodev_root=workflow_stages.AUTODEV_ROOT,
        attempt: int = 0,
        reason: str = "",
        runner=subprocess.run,
        which=workflow_stages.shutil.which,
    ):
        code, payload = original(
            name,
            repo,
            arguments=arguments,
            autodev_root=autodev_root,
            attempt=attempt,
            reason=reason,
            runner=runner,
            which=which,
        )
        if name != "prepare" or payload.get("state") != "CONTINUE":
            return code, payload

        resolved = Path(repo).expanduser().resolve()
        current_dir = resolved / workflow_stages.CURRENT_DIR
        state = workflow_stages.read_state(current_dir)
        execution.enable_protocol(state)
        issue_text = workflow_stages.read_text(current_dir / "issue.md") or str(
            state.get("IssueText", "")
        )
        try:
            report = execution.explicit_classification(issue_text)
        except execution.ExecutionClassificationError as exc:
            raise workflow_stages.WorkflowStageError(
                f"invalid explicit execution classification: {exc}"
            ) from exc
        if report is None:
            state["ExecutionClassification"] = "pending-reader"
            state["ExecutionClassificationSource"] = "reader-required"
            workflow_stages.write_state(current_dir, state)
            return code, payload

        execution.apply_state_fields(state, report)
        workflow_stages.write_state(current_dir, state)
        execution.persist_artifacts(current_dir, report)
        if report.attention_required:
            return 0, _transition_attention(
                resolved,
                current_dir,
                report,
                runner=runner,
            )
        return code, payload

    execute_stage._autodev_execution_classification = True  # type: ignore[attr-defined]
    workflow_stages.execute_stage = execute_stage


def _install_reader_gate() -> None:
    current_prepare = opencode_adapter_handoff._prepare_reader  # type: ignore[attr-defined]
    if not getattr(current_prepare, "_autodev_execution_classification", False):
        original_prepare = current_prepare

        def _prepare_reader(repo: Path, current: Path, issue_text: str) -> str:
            prompt = original_prepare(repo, current, issue_text)
            try:
                state = workflow_stages.read_state(current)
            except workflow_stages.WorkflowStageError:
                state = {}
            if execution.protocol_enabled(state):
                prompt += execution.reader_contract_instructions()
            return prompt

        _prepare_reader._autodev_execution_classification = True  # type: ignore[attr-defined]
        opencode_adapter_handoff._prepare_reader = _prepare_reader  # type: ignore[attr-defined]

    current_accept = opencode_adapter_roles._accept_role_once  # type: ignore[attr-defined]
    if not getattr(current_accept, "_autodev_execution_classification", False):
        original_accept = current_accept

        def _accept_role_once(role: str, current: Path, input_path: Path | None):
            outputs = original_accept(role, current, input_path)
            if role != "reader":
                return outputs
            try:
                state = workflow_stages.read_state(current)
            except workflow_stages.WorkflowStageError:
                return outputs
            if not execution.protocol_enabled(state):
                return outputs
            issue_text = workflow_stages.read_text(current / "issue.md") or str(
                state.get("IssueText", "")
            )
            reader_text = workflow_stages.read_text(current / "reader-brief.md")
            try:
                report = execution.resolve_reader_classification(
                    reader_text,
                    issue_text,
                )
            except execution.ExecutionClassificationError as exc:
                raise opencode_adapter_contract.OpenCodeAdapterError(
                    f"reader execution-classification contract rejected: {exc}"
                ) from exc
            execution.apply_state_fields(state, report)
            workflow_stages.write_state(current, state)
            execution.persist_artifacts(current, report)
            return outputs

        _accept_role_once._autodev_execution_classification = True  # type: ignore[attr-defined]
        opencode_adapter_roles._accept_role_once = _accept_role_once  # type: ignore[attr-defined]


def _attention_resume_payload(
    repo: Path,
    base: dict[str, object] | None = None,
    *,
    runner=subprocess.run,
) -> dict[str, object] | None:
    current = repo / workflow_stages.CURRENT_DIR
    report = execution.load_report(current)
    if report is None or not report.attention_required:
        return None
    payload = _transition_attention(repo, current, report, runner=runner)
    if base:
        for key in (
            "issue_number",
            "branch",
            "run_id",
            "run_dir",
            "commit_sha",
            "pr_url",
        ):
            if base.get(key) and not payload.get(key):
                payload[key] = base[key]
    payload["next_stage"] = ATTENTION_STAGE
    payload["next_action"] = "human manual/external prerequisite"
    return payload


def _install_resume_gates() -> None:
    current_opencode_resume = opencode_resume_execution.resume
    if not getattr(current_opencode_resume, "_autodev_execution_classification", False):
        original_opencode_resume = current_opencode_resume

        def resume(repo: Path, mappings, **kwargs):
            resolved = Path(repo).expanduser().resolve()
            payload = original_opencode_resume(resolved, mappings, **kwargs)
            attention = _attention_resume_payload(
                resolved,
                payload,
                runner=kwargs.get("runner", subprocess.run),
            )
            return attention or payload

        resume._autodev_execution_classification = True  # type: ignore[attr-defined]
        opencode_resume_execution.resume = resume

    current_status = opencode_resume_status.status_text
    if not getattr(current_status, "_autodev_execution_classification", False):
        original_status = current_status

        def status_text(repo: Path, mappings, **kwargs) -> str:
            resolved = Path(repo).expanduser().resolve()
            text = original_status(resolved, mappings, **kwargs)
            report = execution.load_report(resolved / workflow_stages.CURRENT_DIR)
            if report is None:
                return text
            extra = [
                f"Execution classification: {report.classification}",
                f"Execution reason: {report.reason}",
                f"Manual/external attention required: {'yes' if report.attention_required else 'no'}",
            ]
            if report.decomposition_recommended:
                extra.append("Decomposition recommended: yes — create/link an automatable child issue")
            extra.append(
                "Manual action plan: .autodev-run/current/"
                + execution.MANUAL_ACTION_PLAN_FILE
            )
            return text.rstrip() + "\n" + "\n".join(extra) + "\n"

        status_text._autodev_execution_classification = True  # type: ignore[attr-defined]
        opencode_resume_status.status_text = status_text

    current_role_resume = role_resume.resume
    if not getattr(current_role_resume, "_autodev_execution_classification", False):
        original_role_resume = current_role_resume

        def resume(repo: Path, snapshots, **kwargs):
            resolved = Path(repo).expanduser().resolve()
            payload = original_role_resume(resolved, snapshots, **kwargs)
            attention = _attention_resume_payload(
                resolved,
                payload,
                runner=kwargs.get("runner", subprocess.run),
            )
            return attention or payload

        resume._autodev_execution_classification = True  # type: ignore[attr-defined]
        role_resume.resume = resume


def _install_python_coordinator_gate() -> None:
    current_resume_payload = role_coordinator_flow._resume_payload  # type: ignore[attr-defined]
    if not getattr(current_resume_payload, "_autodev_execution_classification", False):
        original_resume_payload = current_resume_payload

        def _resume_payload(*args, **kwargs):
            payload = original_resume_payload(*args, **kwargs)
            if payload.get("state") == ATTENTION_STATE:
                raise _AttentionRequired(payload)
            return payload

        _resume_payload._autodev_execution_classification = True  # type: ignore[attr-defined]
        role_coordinator_flow._resume_payload = _resume_payload  # type: ignore[attr-defined]

    current_terminal = role_coordinator_flow.terminal_payload
    if not getattr(current_terminal, "_autodev_execution_classification", False):
        original_terminal = current_terminal

        def terminal_payload(repo: Path, payload, **kwargs):
            if payload.get("state") == ATTENTION_STATE:
                return dict(payload)
            return original_terminal(repo, payload, **kwargs)

        terminal_payload._autodev_execution_classification = True  # type: ignore[attr-defined]
        role_coordinator_flow.terminal_payload = terminal_payload

    current_coordinate = role_coordinator_flow.coordinate
    if not getattr(current_coordinate, "_autodev_execution_classification", False):
        original_coordinate = current_coordinate

        def coordinate(*args, **kwargs):
            try:
                return original_coordinate(*args, **kwargs)
            except _AttentionRequired as attention:
                return dict(attention.payload)

        coordinate._autodev_execution_classification = True  # type: ignore[attr-defined]
        role_coordinator_flow.coordinate = coordinate


def install() -> None:
    _install_queue_label_bootstrap()
    _install_queue_evidence_reconciliation()
    _install_prepare_gate()
    _install_reader_gate()
    _install_resume_gates()
    _install_python_coordinator_gate()
