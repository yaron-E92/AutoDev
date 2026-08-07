---
description: Apply one targeted AutoDev repair in an isolated fixer context
agent: autodev-fixer
subtask: true
---
Use the installed AutoDev bridge for issue/task `$ARGUMENTS`.

1. Run `pwsh -NoProfile -File .opencode/autodev.ps1 prepare --role fixer --arguments "$ARGUMENTS"`.
2. Read `.codex-run/current/fixer.md` and follow that generated AutoDev repair prompt exactly.
3. Edit only the target repository files required by that repair.
4. Run `pwsh -NoProfile -File .opencode/autodev.ps1 accept --role fixer`.

Do not create/switch branches, commit, push, edit issue labels, create pull requests, or delegate to another agent.
