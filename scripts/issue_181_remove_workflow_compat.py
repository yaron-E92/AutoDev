from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(text: str, old: str, new: str, *, path: Path) -> str:
    if old not in text:
        raise SystemExit(f"missing expected text in {path}: {old!r}")
    return text.replace(old, new, 1)


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


def main() -> None:
    remove_facade_adapter()
    migrate_ci_outcomes()
    migrate_pr_head_sync()
    migrate_opencode_runtime()


if __name__ == "__main__":
    main()
