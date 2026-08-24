from __future__ import annotations

from automation import privacy_grant_cli, privacy_grant_hooks, queue_cli, repair_budget_resume

from automation import windows_verification_hooks

import sys

from automation import ci_outcomes, context_optimization, execution_classification_evidence, execution_classification_hooks, opencode_github_entrypoint, opencode_role_entrypoint, opencode_runtime, pr_head_sync, privacy_consent, role_workflow_hooks, windows_semantic_order


COORDINATE_COMMAND = "coordinate"
ROLE_COMMAND = "role"
PRIVACY_COMMAND = "privacy"
QUEUE_COMMAND = "queue"


def run(argv: list[str] | None = None) -> int:
    ci_outcomes.install()
    pr_head_sync.install()
    repair_budget_resume.install_opencode_resume_hooks()
    windows_verification_hooks.install_opencode_hooks()
    windows_semantic_order.install()
    context_optimization.install()
    privacy_consent.install()
    privacy_grant_hooks.install(run_consent=True)
    # Install runtime-neutral workflow policy on the canonical coordinator
    # after the underlying workflow patches are active.
    role_workflow_hooks.install()
    # Manual/external execution classification must wrap the final shared role
    # and resume boundaries so both slash-command and Python coordination stop
    # before implementation when attention is required.
    execution_classification_hooks.install()
    # Re-check only the explicit secret-free completion signal on resume, then
    # return to Reader so the remaining work is classified from fresh evidence.
    execution_classification_evidence.install()
    values = list(sys.argv[1:] if argv is None else argv)
    if values and values[0] == PRIVACY_COMMAND:
        return privacy_grant_cli.run_cli(values[1:])
    if values and values[0] == QUEUE_COMMAND:
        return queue_cli.run_cli(values[1:])
    if values and values[0] == COORDINATE_COMMAND:
        return opencode_github_entrypoint.run(values[1:])
    if values and values[0] == ROLE_COMMAND:
        return opencode_role_entrypoint.run(values[1:])
    return opencode_runtime.run(values)


def main() -> int:
    return run()


if __name__ == "__main__":
    raise SystemExit(main())