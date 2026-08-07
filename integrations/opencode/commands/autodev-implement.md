---
description: Implement one prepared AutoDev issue in an isolated editor context
agent: autodev-implementer
subtask: true
---
Use the installed AutoDev bridge for issue/task `$ARGUMENTS`.

1. Run `pwsh -NoProfile -File .opencode/autodev.ps1 prepare --role implementer --arguments "$ARGUMENTS"`.
2. Read `.codex-run/current/implementer.md` and follow that generated AutoDev prompt exactly.
3. Edit only the target repository files required by that prompt.
4. Write a concise imperative commit message to `.codex-run/current/commit-message.txt`.
5. Run `pwsh -NoProfile -File .opencode/autodev.ps1 accept --role implementer`.

Do not create/switch branches, commit, push, edit issue labels, create pull requests, or delegate to another agent.
