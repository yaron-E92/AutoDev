from __future__ import annotations

from pathlib import Path


WORKFLOW = Path("automation/workflow_stages.py")
WINDOWS_HOOKS = Path("automation/windows_workflow_hooks.py")


def patch_workflow() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    old_imports = '''from automation import semantic_repair_budget as _semantic_budget\nfrom automation import workspace_scope\nfrom automation import windows_workflow_hooks as _windows_workflow_hooks\n'''
    new_imports = '''from automation import repair_budget_contract as _budget_contract\nfrom automation import repair_budget_failure as _budget_failure\nfrom automation import repair_budget_policy as _budget_policy\nfrom automation import repair_budget_storage as _budget_storage\nfrom automation import workspace_scope\n'''
    if old_imports in text:
        text = text.replace(old_imports, new_imports, 1)

    replacements = {
        "_semantic_budget._nonnegative_int": "_budget_policy._nonnegative_int",
        "_semantic_budget.validate_config": "_budget_policy.validate_config",
        "_semantic_budget.resolve_budget": "_budget_policy.resolve_budget",
        "_semantic_budget.persist_budget": "_budget_storage.persist_budget",
        "_semantic_budget.persist_failure": "_budget_storage.persist_failure",
        "_semantic_budget.clear_failure_state": "_budget_storage.clear_failure_state",
        "_semantic_budget.failure_details": "_budget_failure.failure_details",
        "_semantic_budget.concise_failure_reason": "_budget_failure.concise_failure_reason",
        "_semantic_budget.human_failure_summary": "_budget_failure.human_failure_summary",
        "_semantic_budget.SemanticRepairBudgetError": "_budget_contract.SemanticRepairBudgetError",
        "_semantic_budget.FIXED_LIMIT_ENV": "_budget_contract.FIXED_LIMIT_ENV",
        "_semantic_budget.DEFAULT_ADAPTIVE_MAX": "_budget_contract.DEFAULT_ADAPTIVE_MAX",
        "_semantic_budget.ADAPTIVE_MAX_ENV": "_budget_contract.ADAPTIVE_MAX_ENV",
        "_semantic_budget.DEFAULT_ADAPTIVE_MIN": "_budget_contract.DEFAULT_ADAPTIVE_MIN",
        "_semantic_budget.FAILURE_REPAIR_BUDGET_EXHAUSTED": "_budget_contract.FAILURE_REPAIR_BUDGET_EXHAUSTED",
        "_semantic_budget.ROOT_FAILURE_CLASSIFICATION": "_budget_contract.ROOT_FAILURE_CLASSIFICATION",
    }
    for before, after in replacements.items():
        text = text.replace(before, after)

    old_assignment = "execute_stage = _execute_stage\nmark_blocked = _mark_blocked\nFAILURE_REPAIR_BUDGET_EXHAUSTED = _budget_contract.FAILURE_REPAIR_BUDGET_EXHAUSTED\n"
    new_assignment = '''_WORKFLOW_EXECUTOR = _execute_stage\n_POLICY_HOOKS_INSTALLED = False\n\n\ndef _ensure_policy_hooks() -> None:\n    global _POLICY_HOOKS_INSTALLED, _WORKFLOW_EXECUTOR\n    if _POLICY_HOOKS_INSTALLED:\n        return\n    from automation import repair_budget_manifest\n    from automation import windows_workflow_hooks\n\n    _budget_policy._resume_budget = _resume_semantic_budget\n    repair_budget_manifest.install_run_manifest_hooks()\n    _WORKFLOW_EXECUTOR = windows_workflow_hooks.build_execute_stage(\n        sys.modules[__name__],\n        _execute_stage,\n    )\n    _POLICY_HOOKS_INSTALLED = True\n\n\ndef execute_stage(\n    name: str,\n    repo: Path,\n    *,\n    arguments: str = "",\n    autodev_root: Path = AUTODEV_ROOT,\n    attempt: int = 0,\n    reason: str = "",\n    runner=subprocess.run,\n    which=shutil.which,\n) -> tuple[int, dict[str, object]]:\n    _ensure_policy_hooks()\n    return _WORKFLOW_EXECUTOR(\n        name,\n        repo,\n        arguments=arguments,\n        autodev_root=autodev_root,\n        attempt=attempt,\n        reason=reason,\n        runner=runner,\n        which=which,\n    )\n\n\nmark_blocked = _mark_blocked\nFAILURE_REPAIR_BUDGET_EXHAUSTED = _budget_contract.FAILURE_REPAIR_BUDGET_EXHAUSTED\n'''
    if old_assignment in text:
        text = text.replace(old_assignment, new_assignment, 1)

    old_hooks = '''_semantic_budget._resume_budget = _resume_semantic_budget  # type: ignore[attr-defined]\n_semantic_budget.install_run_manifest_hooks()\n_windows_workflow_hooks.install(sys.modules[__name__])\n'''
    if old_hooks in text:
        text = text.replace(old_hooks, "", 1)

    if "_semantic_budget" in text or "_windows_workflow_hooks" in text:
        raise SystemExit("workflow_stages still contains eager policy-hook dependencies")
    if "def _ensure_policy_hooks()" not in text:
        raise SystemExit("workflow_stages lazy policy hook dispatcher was not installed")
    WORKFLOW.write_text(text, encoding="utf-8")


def patch_windows_hooks() -> None:
    text = WINDOWS_HOOKS.read_text(encoding="utf-8")
    text = text.replace("from automation import windows_verification\n\n\n", "", 1)
    text = text.replace(
        "def install(core) -> None:\n    \"\"\"Layer GitHub-hosted Windows verification onto the shared workflow API.\"\"\"\n\n    if getattr(core, \"_autodev_windows_workflow_hooks_installed\", False):\n        return\n    windows_verification.install_manifest_hooks()\n    original_execute_stage = core.execute_stage\n",
        "def build_execute_stage(core, original_execute_stage):\n    \"\"\"Return a Windows-aware workflow executor without mutating the facade.\"\"\"\n\n    from automation import windows_verification\n\n    windows_verification.install_manifest_hooks()\n",
        1,
    )
    old_tail = '''    core.execute_stage = execute_stage\n    core._autodev_windows_workflow_hooks_installed = True\n'''
    new_tail = '''    return execute_stage\n\n\ndef install(core) -> None:\n    \"\"\"Compatibility installer for callers that still request mutation explicitly.\"\"\"\n\n    if getattr(core, "_autodev_windows_workflow_hooks_installed", False):\n        return\n    core.execute_stage = build_execute_stage(core, core.execute_stage)\n    core._autodev_windows_workflow_hooks_installed = True\n'''
    if old_tail in text:
        text = text.replace(old_tail, new_tail, 1)
    if "from automation import windows_verification" in text.split("def build_execute_stage", 1)[0]:
        raise SystemExit("windows workflow hooks still import verification at module import time")
    if "def build_execute_stage" not in text:
        raise SystemExit("windows workflow hook builder was not installed")
    WINDOWS_HOOKS.write_text(text, encoding="utf-8")


def main() -> None:
    patch_workflow()
    patch_windows_hooks()


if __name__ == "__main__":
    main()
