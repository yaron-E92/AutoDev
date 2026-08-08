---
description: Resume an interrupted AutoDev issue-to-PR run from durable checkpoints
agent: autodev-coordinator
subtask: false
---
Resume the current AutoDev OpenCode run from its `.autodev-run/current/run-manifest.json` checkpoint state.

Run the installed bridge (use `python3` instead where required):

```text
python .opencode/autodev.py resume
```

If `$ARGUMENTS` contains one or more `--invalidate-role <role>` values, append only those explicit invalidations. Do not pass model override flags; model changes come from the normal OpenCode configuration resolved by #66.

Use the returned `next_action` and durable repair counters to enter the existing `autodev-coordinator` workflow at that exact boundary. Do not restart preflight/prepare or rerun a completed reader/planner/implementer/verification stage unless the manifest invalidated it. Finish with the same `PR_READY`, `BLOCKED`, or `FAILED` contract as a normal run.
