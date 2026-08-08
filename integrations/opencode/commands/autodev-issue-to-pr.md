---
description: Run one AutoDev issue from GitHub issue number to PR ready for review
agent: autodev-coordinator
subtask: false
---
Run the AutoDev one-command issue-to-PR workflow for `$ARGUMENTS`.

Follow the `autodev-coordinator` contract exactly. Keep model-heavy work in the isolated AutoDev role subagents, pass state through `.autodev-run/current` artifacts, use only the installed AutoDev bridge for workflow stages, and finish with exactly one explicit final state: `PR_READY`, `BLOCKED`, or `FAILED`.

Never merge the pull request.
