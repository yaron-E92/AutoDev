---
description: Run one AutoDev issue from GitHub issue number to PR ready for review
agent: autodev-coordinator
subtask: false
---
Run the AutoDev one-command issue-to-PR workflow for `$ARGUMENTS`.

Follow the `autodev-coordinator` contract exactly. Keep model-heavy work in the isolated AutoDev role subagents, pass state through `.autodev-run/current` artifacts, use only the installed AutoDev bridge for workflow stages, and finish with exactly one explicit final state: `PR_READY`, `BLOCKED`, or `FAILED`.

At startup, read `.opencode/autodev.json` and use its configured non-empty `python` field as the exact launcher. Do not probe alternate Python commands, use absolute bridge/interpreter paths, `cd`, temporary bridge copies, shell wrappers, or exploratory repository commands. All bridge paths are relative to the active repository; its own `.opencode` directory is installer-owned workflow state, not an unrelated external workspace.

After every delegated AutoDev role Task, do not trust the Task UI checkmark or child-agent prose as proof of completion. Run exactly the repository-relative role-check bridge command `role-check --role <role>` with the configured launcher. Advance only when its JSON state is `ACCEPTED`. `MISSING`, `STALE`, denial, or non-JSON output is a failed role boundary; stop with `FAILED` rather than inferring completion, inventing a Task ID, reconstructing workflow state manually, or launching a dependent role.

For `/autodev-resume`, the resume bridge JSON remains the sole authority for the continuation boundary before any role is delegated. Do not replace its returned `next_action`, `next_role`, or `next_stage` with model inference.

When a bridge stage has already returned a concrete `FAILED` payload, preserve that originating issue number, failed stage, classification, reason, and fingerprint when entering the terminal failed boundary. Do not replace a precise failure with a generic coordinator failure.

Never merge the pull request.
