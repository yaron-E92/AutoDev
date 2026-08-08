---
description: Show durable AutoDev issue-to-PR run status and resume safety
agent: autodev-coordinator
subtask: false
---
Show the durable status of the current AutoDev OpenCode run.

Run the installed bridge as `python .opencode/autodev.py status` (or `python3` where required). If `$ARGUMENTS` contains one or more `--invalidate-role <role>` values, append only those flags to preview their #37 invalidation consequences. Do not mutate workflow state, rerun completed work, or invoke a child Task.

Return the bridge status compactly, including resume blockers and the next model role/model when present.
