from __future__ import annotations

from pathlib import Path


PATH = Path("automation/workflow_stages.py")


def replace_once(text: str, old: str, new: str) -> str:
    if text.count(old) != 1:
        raise SystemExit(f"expected exactly one occurrence of {old!r}")
    return text.replace(old, new, 1)


def main() -> None:
    text = PATH.read_text(encoding="utf-8")
    text = replace_once(
        text,
        "import copy\nimport json\n",
        "import copy\nimport functools\nimport inspect\nimport json\n",
    )
    text = replace_once(
        text,
        "from automation import semantic_repair_budget as _semantic_budget\n",
        "from automation import semantic_repair_budget as _semantic_budget\nfrom automation import workspace_scope\n",
    )
    text = replace_once(
        text,
        "from automation import workflow_dispatch as _workflow_dispatch\n"
        "from automation import workflow_github as _workflow_github\n"
        "from automation import workflow_verification as _workflow_verification\n",
        "from automation import workflow_commands as _workflow_commands\n"
        "from automation import workflow_contract as _workflow_contract\n"
        "from automation import workflow_diagnostics as _workflow_diagnostics\n"
        "from automation import workflow_dispatch as _workflow_dispatch\n"
        "from automation import workflow_github as _workflow_github\n"
        "from automation import workflow_preparation as _workflow_preparation\n"
        "from automation import workflow_prompts as _workflow_prompts\n"
        "from automation import workflow_storage as _workflow_storage\n"
        "from automation import workflow_verification as _workflow_verification\n"
        "from automation import workflow_workspace as _workflow_workspace\n",
    )
    text = replace_once(
        text,
        "from automation.semantic_verifier import SemanticVerifierError\n",
        "from automation.semantic_verifier import (\n"
        "    SemanticVerifierError,\n"
        "    extract_acceptance_criteria,\n"
        "    parse_semantic_output,\n"
        "    prepare_semantic_repair_prompt,\n"
        "    render_template,\n"
        ")\n",
    )

    marker = (
        "FAILURE_REPAIR_BUDGET_EXHAUSTED = _semantic_budget.FAILURE_REPAIR_BUDGET_EXHAUSTED\n\n"
        "# Explicitly install the cross-cutting compatibility boundaries"
    )
    compat = '''FAILURE_REPAIR_BUDGET_EXHAUSTED = _semantic_budget.FAILURE_REPAIR_BUDGET_EXHAUSTED

# The pre-refactor module was deliberately monkeypatch-friendly: tests and a few
# extension hooks replace attributes on automation.workflow_stages. Keep that
# public seam without making the responsibility modules depend back on this
# facade. Before a facade entrypoint delegates, matching facade overrides are
# copied into the modules that consume them. Production dependency direction
# therefore remains one-way; this adapter exists only at the compatibility edge.
_WORKFLOW_MODULES = (
    _workflow_contract,
    _workflow_storage,
    _workflow_commands,
    _workflow_workspace,
    _workflow_prompts,
    _workflow_diagnostics,
    _workflow_github,
    _workflow_preparation,
    _workflow_verification,
    _workflow_dispatch,
)


def _sync_compat_overrides() -> None:
    facade = globals()
    for module in _WORKFLOW_MODULES:
        namespace = module.__dict__
        for name in tuple(namespace):
            if name.startswith("__") or name not in facade:
                continue
            namespace[name] = facade[name]


def _compat_entrypoint(target):
    @functools.wraps(target)
    def invoke(*args, **kwargs):
        _sync_compat_overrides()
        return target(*args, **kwargs)

    return invoke


def _install_compat_entrypoints() -> None:
    facade = globals()
    wrapped: set[str] = set()
    for module in _WORKFLOW_MODULES:
        for name in tuple(module.__dict__):
            if name in wrapped or name.startswith("__") or name not in facade:
                continue
            value = facade[name]
            if inspect.isfunction(value) and value.__module__.startswith("automation."):
                facade[name] = _compat_entrypoint(value)
                wrapped.add(name)


_install_compat_entrypoints()

# Explicitly install the cross-cutting compatibility boundaries'''
    text = replace_once(text, marker, compat)
    PATH.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
