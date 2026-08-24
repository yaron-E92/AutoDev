from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(text: str, old: str, new: str, *, path: Path) -> str:
    if old not in text:
        raise SystemExit(f"missing expected text in {path}: {old!r}")
    return text.replace(old, new, 1)


def replace_in_test(text: str, test_name: str, old: str, new: str, *, path: Path) -> str:
    marker = f"    def {test_name}("
    start = text.find(marker)
    if start < 0:
        raise SystemExit(f"missing test {test_name} in {path}")
    end = text.find("\n    def ", start + len(marker))
    if end < 0:
        end = len(text)
    block = text[start:end]
    if old not in block:
        raise SystemExit(f"missing {old!r} in {test_name} ({path})")
    block = block.replace(old, new)
    return text[:start] + block + text[end:]


def remove_facade_adapter() -> None:
    path = ROOT / "automation/workflow_stages.py"
    text = path.read_text(encoding="utf-8")
    text = text.replace("import functools\n", "")
    text = text.replace("import inspect\n", "")
    start_marker = "# The pre-refactor module was deliberately monkeypatch-friendly:"
    end_marker = "# Explicitly install the cross-cutting compatibility boundaries in the modules\n"
    start = text.find(start_marker)
    end = text.find(end_marker, start)
    if start < 0 or end < 0:
        raise SystemExit("workflow compatibility adapter markers missing")
    text = text[:start] + end_marker + text[end + len(end_marker):]
    path.write_text(text, encoding="utf-8")


def migrate_ci_outcomes() -> None:
    path = ROOT / "automation/ci_outcomes.py"
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        "from automation import workflow_stages\n",
        "from automation import workflow_dispatch, workflow_github, workflow_stages, workflow_verification\n",
        path=path,
    )
    text = text.replace(
        "current_wait = workflow_stages.wait_for_required_checks",
        "current_wait = workflow_verification.wait_for_required_checks",
    )
    text = text.replace(
        "        workflow_stages.wait_for_required_checks = wait_for_required_checks\n",
        "        workflow_stages.wait_for_required_checks = wait_for_required_checks\n"
        "        workflow_github.wait_for_required_checks = wait_for_required_checks\n"
        "        workflow_verification.wait_for_required_checks = wait_for_required_checks\n",
    )
    text = text.replace(
        "current_pr_and_ci = workflow_stages.pr_and_ci",
        "current_pr_and_ci = workflow_dispatch.pr_and_ci",
    )
    text = text.replace(
        "        workflow_stages.pr_and_ci = pr_and_ci\n",
        "        workflow_stages.pr_and_ci = pr_and_ci\n"
        "        workflow_verification.pr_and_ci = pr_and_ci\n"
        "        workflow_dispatch.pr_and_ci = pr_and_ci\n",
    )
    text = text.replace(
        "current_ci_state = workflow_stages._ci_state",
        "current_ci_state = workflow_github._ci_state",
    )
    text = text.replace(
        "        workflow_stages._ci_state = guarded_ci_state\n",
        "        workflow_stages._ci_state = guarded_ci_state\n"
        "        workflow_github._ci_state = guarded_ci_state\n",
    )
    text = text.replace(
        "current_validate_ready = workflow_stages.validate_ready_proof",
        "current_validate_ready = workflow_github.validate_ready_proof",
    )
    text = text.replace(
        "        workflow_stages.validate_ready_proof = guarded_validate_ready_proof\n",
        "        workflow_stages.validate_ready_proof = guarded_validate_ready_proof\n"
        "        workflow_github.validate_ready_proof = guarded_validate_ready_proof\n"
        "        workflow_dispatch.validate_ready_proof = guarded_validate_ready_proof\n",
    )
    path.write_text(text, encoding="utf-8")


def migrate_pr_head_sync() -> None:
    path = ROOT / "automation/pr_head_sync.py"
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        "from automation import workflow_stages\n",
        "from automation import workflow_stages, workflow_verification\n",
        path=path,
    )
    text = text.replace(
        "    current = workflow_stages.ensure_pr\n",
        "    current = workflow_verification.ensure_pr\n",
    )
    text = text.replace(
        "    workflow_stages.ensure_pr = ensure_pr\n",
        "    workflow_stages.ensure_pr = ensure_pr\n"
        "    workflow_verification.ensure_pr = ensure_pr\n",
    )
    path.write_text(text, encoding="utf-8")


def migrate_opencode_runtime() -> None:
    path = ROOT / "automation/opencode_runtime.py"
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        "from automation import workflow_stages\n",
        "from automation import workflow_dispatch, workflow_stages, workflow_verification, workflow_workspace\n",
        path=path,
    )
    text = text.replace(
        "    current_source_identity = workflow_stages.source_identity\n",
        "    current_source_identity = workflow_verification.source_identity\n",
    )
    text = text.replace(
        "        workflow_stages.source_identity = source_identity\n",
        "        workflow_stages.source_identity = source_identity\n"
        "        workflow_verification.source_identity = source_identity\n"
        "        workflow_dispatch.source_identity = source_identity\n",
    )
    text = text.replace(
        "    current_ensure_pr = workflow_stages.ensure_pr\n",
        "    current_ensure_pr = workflow_verification.ensure_pr\n",
    )
    text = text.replace(
        "        workflow_stages.ensure_pr = ensure_pr\n",
        "        workflow_stages.ensure_pr = ensure_pr\n"
        "        workflow_verification.ensure_pr = ensure_pr\n",
    )
    text = text.replace(
        "    current_pr_and_ci = workflow_stages.pr_and_ci\n",
        "    current_pr_and_ci = workflow_dispatch.pr_and_ci\n",
    )
    text = text.replace(
        "        workflow_stages.pr_and_ci = pr_and_ci\n",
        "        workflow_stages.pr_and_ci = pr_and_ci\n"
        "        workflow_verification.pr_and_ci = pr_and_ci\n"
        "        workflow_dispatch.pr_and_ci = pr_and_ci\n",
    )
    text = text.replace(
        "        workflow_stages.ignored_workspace_path = ignored_workspace_path\n",
        "        workflow_stages.ignored_workspace_path = ignored_workspace_path\n"
        "        workflow_workspace.ignored_workspace_path = ignored_workspace_path\n",
    )
    path.write_text(text, encoding="utf-8")


def retarget_workflow_stage_tests() -> None:
    path = ROOT / "tests/test_workflow_stages.py"
    text = path.read_text(encoding="utf-8")

    for test_name in (
        "test_prepare_writes_cross_platform_state_without_model_calls",
        "test_prepare_rejects_missing_remote_base_tree_before_issue_mutation",
        "test_prepare_reuses_matching_current_issue_without_github_mutation",
    ):
        if "gh_json" in text[text.find(f"    def {test_name}("):]:
            try:
                text = replace_in_test(
                    text, test_name,
                    "automation.workflow_stages.gh_json",
                    "automation.workflow_preparation.gh_json",
                    path=path,
                )
            except SystemExit:
                pass
        try:
            text = replace_in_test(
                text, test_name,
                "automation.workflow_stages.gh",
                "automation.workflow_preparation.gh",
                path=path,
            )
        except SystemExit:
            pass
        try:
            text = replace_in_test(
                text, test_name,
                "automation.workflow_stages.validate_prepared_worktree",
                "automation.workflow_preparation.validate_prepared_worktree",
                path=path,
            )
        except SystemExit:
            pass

    for test_name in (
        "test_create_api_commit_recovers_and_persists_missing_prepared_tree",
        "test_create_api_commit_missing_parent_tree_keeps_underlying_evidence",
        "test_api_commit_persists_verified_tree_parent_and_source_identity",
        "test_ensure_pr_reuses_existing_pr_without_creating_duplicate",
        "test_ready_requires_durable_shipped_tree_and_terminal_ci_proof",
        "test_ready_and_blocked_reuse_existing_issue_state_contract",
    ):
        try:
            text = replace_in_test(
                text, test_name,
                "automation.workflow_stages.gh_json",
                "automation.workflow_github.gh_json",
                path=path,
            )
        except SystemExit:
            pass
        try:
            text = replace_in_test(
                text, test_name,
                "automation.workflow_stages.gh",
                "automation.workflow_github.gh",
                path=path,
            )
        except SystemExit:
            pass

    text = text.replace(
        "automation.workflow_stages.prepare_semantic_repair_prompt",
        "automation.workflow_dispatch.prepare_semantic_repair_prompt",
    )
    text = text.replace(
        "automation.workflow_stages.create_api_commit",
        "automation.workflow_verification.create_api_commit",
    )
    text = text.replace(
        "automation.workflow_stages.ensure_pr",
        "automation.workflow_verification.ensure_pr",
    )
    text = text.replace(
        "automation.workflow_stages.wait_for_required_checks",
        "automation.workflow_verification.wait_for_required_checks",
    )
    text = text.replace(
        "automation.workflow_stages._pr_head_sha",
        "automation.workflow_github._pr_head_sha",
    )
    text = text.replace(
        "automation.workflow_stages._query_pr_checks",
        "automation.workflow_github._query_pr_checks",
    )
    text = replace_in_test(
        text,
        "test_ci_failure_renders_repair_and_coordinator_maps_exhaustion",
        "automation.workflow_stages.pr_and_ci",
        "automation.workflow_dispatch.pr_and_ci",
        path=path,
    )
    path.write_text(text, encoding="utf-8")


def retarget_ci_outcome_tests() -> None:
    path = ROOT / "tests/test_ci_outcomes.py"
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        "from automation import ci_outcomes, workflow_stages\n",
        "from automation import ci_outcomes, workflow_dispatch, workflow_github, workflow_stages, workflow_verification\n",
        path=path,
    )
    old = '''    def _install_originals(self) -> dict[str, object]:
        return {
            "ci_state": workflow_stages._ci_state,
            "validate_ready": workflow_stages.validate_ready_proof,
            "wait_for_checks": workflow_stages.wait_for_required_checks,
            "pr_and_ci": workflow_stages.pr_and_ci,
            "execute_stage": workflow_stages.execute_stage,
        }

    def _restore_install_originals(self, originals: dict[str, object]) -> None:
        workflow_stages._ci_state = originals["ci_state"]  # type: ignore[assignment]
        workflow_stages.validate_ready_proof = originals["validate_ready"]  # type: ignore[assignment]
        workflow_stages.wait_for_required_checks = originals["wait_for_checks"]  # type: ignore[assignment]
        workflow_stages.pr_and_ci = originals["pr_and_ci"]  # type: ignore[assignment]
        workflow_stages.execute_stage = originals["execute_stage"]  # type: ignore[assignment]
'''
    new = '''    def _install_originals(self) -> dict[str, object]:
        return {
            "ci_state": workflow_github._ci_state,
            "validate_ready": workflow_github.validate_ready_proof,
            "wait_for_checks": workflow_verification.wait_for_required_checks,
            "pr_and_ci": workflow_dispatch.pr_and_ci,
            "execute_stage": workflow_stages.execute_stage,
        }

    def _restore_install_originals(self, originals: dict[str, object]) -> None:
        workflow_stages._ci_state = originals["ci_state"]  # type: ignore[assignment]
        workflow_github._ci_state = originals["ci_state"]  # type: ignore[assignment]
        workflow_stages.validate_ready_proof = originals["validate_ready"]  # type: ignore[assignment]
        workflow_github.validate_ready_proof = originals["validate_ready"]  # type: ignore[assignment]
        workflow_dispatch.validate_ready_proof = originals["validate_ready"]  # type: ignore[assignment]
        workflow_stages.wait_for_required_checks = originals["wait_for_checks"]  # type: ignore[assignment]
        workflow_github.wait_for_required_checks = originals["wait_for_checks"]  # type: ignore[assignment]
        workflow_verification.wait_for_required_checks = originals["wait_for_checks"]  # type: ignore[assignment]
        workflow_stages.pr_and_ci = originals["pr_and_ci"]  # type: ignore[assignment]
        workflow_verification.pr_and_ci = originals["pr_and_ci"]  # type: ignore[assignment]
        workflow_dispatch.pr_and_ci = originals["pr_and_ci"]  # type: ignore[assignment]
        workflow_stages.execute_stage = originals["execute_stage"]  # type: ignore[assignment]
'''
    text = replace_once(text, old, new, path=path)
    text = text.replace(
        "                workflow_stages.pr_and_ci = pending_pr_and_ci\n",
        "                workflow_dispatch.pr_and_ci = pending_pr_and_ci\n",
    )
    text = text.replace(
        "automation.workflow_stages._pr_head_sha",
        "automation.workflow_github._pr_head_sha",
    )
    text = text.replace(
        "automation.workflow_stages.gh_json",
        "automation.workflow_github.gh_json",
    )
    path.write_text(text, encoding="utf-8")


def retarget_semantic_default_tests() -> None:
    path = ROOT / "tests/test_semantic_repair_default.py"
    text = path.read_text(encoding="utf-8")
    text = text.replace(
        "automation.workflow_stages._require_accepted_role",
        "automation.workflow_dispatch._require_accepted_role",
    )
    text = text.replace(
        "automation.workflow_stages.prepare_semantic_repair_prompt",
        "automation.workflow_dispatch.prepare_semantic_repair_prompt",
    )
    path.write_text(text, encoding="utf-8")


def retarget_shipped_pr_test() -> None:
    path = ROOT / "tests/test_opencode_shipped_pr_and_ci.py"
    text = path.read_text(encoding="utf-8")
    text = text.replace(
        'patch.object(workflow_stages, "wait_for_required_checks", return_value=ci),',
        'patch("automation.workflow_verification.wait_for_required_checks", return_value=ci),',
    )
    path.write_text(text, encoding="utf-8")


def main() -> None:
    remove_facade_adapter()
    migrate_ci_outcomes()
    migrate_pr_head_sync()
    migrate_opencode_runtime()
    retarget_workflow_stage_tests()
    retarget_ci_outcome_tests()
    retarget_semantic_default_tests()
    retarget_shipped_pr_test()


if __name__ == "__main__":
    main()
