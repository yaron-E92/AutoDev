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

After every delegated role Task, do not trust the Task UI checkmark or the child agent's prose as completion proof. Run the exact resume bridge again and advance only when its authoritative `next_action` proves the expected durable role checkpoint was accepted. If it still names the same role after the Task returned success, treat that as missing/unaccepted durable progress and finish `FAILED` rather than launching the dependent role.

Finish with exactly one explicit final state: `PR_READY`, `BLOCKED`, or `FAILED`. Never merge the pull request.
