---
description: Resume an interrupted AutoDev issue-to-PR run from durable checkpoints
agent: autodev-coordinator
subtask: false
---
Resume the current AutoDev OpenCode run from its `.autodev-run/current/run-manifest.json` checkpoint state.

First read `.opencode/autodev.json` and use its non-empty `python` field as the exact launcher. Do not probe `python`, `python3`, absolute interpreter paths, temporary directories, shell wrappers, `cd`, `bash -c`, or fallback commands.

Run exactly one installed resume bridge command, substituting only that configured launcher for the leading `python` token:

```text
python .opencode/autodev.py resume
```

If `$ARGUMENTS` contains one or more `--invalidate-role <role>` values, append only those explicit invalidations. Do not pass model override flags; model changes come from the normal OpenCode configuration resolved by #66.

The resume bridge JSON is the sole authority for the continuation boundary. Use only its `state`, `next_action`, `next_role`, `next_stage`, and durable repair counters. Never infer a completed role, invent a Task ID, reconstruct progress from chat history, or derive a continuation boundary by reading the manifest yourself.

If the exact resume bridge command is denied, cannot be launched, returns invalid/non-JSON output, or otherwise does not produce an authoritative resume payload, do not try alternate shell commands and do not continue manually. Finish `FAILED` with the concrete bridge/permission reason. A failed resume invocation is not permission to restart preflight/prepare.

On a successful `state: RESUME`, enter the existing `autodev-coordinator` workflow at exactly the returned `next_action`. Do not restart preflight/prepare or rerun a completed reader/planner/implementer/verification stage unless the bridge explicitly invalidated it.

When delegating the returned AutoDev role, do not compose a new role procedure or add repository/implementation claims. The Task instruction must be only: `Follow your installed autodev-<role> contract exactly for the current run. Return only success/failure and the accepted artifact path.` The child role contract owns its prepare/read/write/accept sequence.

After every delegated role Task returns, ignore its UI checkmark and prose as workflow state. **The very next tool invocation must be exactly the repository-relative `role-check --role <role>` bridge operation with the configured launcher.** Do not read artifacts/manifests/state, run status/resume/stages, issue another Task, or try exploratory Bash between the Task return and that role-check. Advance only on JSON state `ACCEPTED`. `MISSING`, `STALE`, denial, or invalid output is a failed role boundary; finish `FAILED` instead of launching a dependent role, rerunning preflight/prepare, or reconstructing the continuation boundary. Use `resume` again only when a new continuation boundary must be calculated after a durable stage transition, not as a substitute for role acceptance validation.

Finish with exactly one explicit final state: `PR_READY`, `BLOCKED`, or `FAILED`. Never merge the pull request.
