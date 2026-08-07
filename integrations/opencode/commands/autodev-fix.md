---
description: Apply one targeted AutoDev repair in an isolated fixer context
agent: autodev-fixer
subtask: true
---
Use the installed portable AutoDev bridge for issue/task `$ARGUMENTS`.

1. Run `python .opencode/autodev.py prepare --role fixer --arguments "$ARGUMENTS"` (use `python3` instead where that is the available Python command).
2. Read `.codex-run/current/fixer.md` and follow that generated AutoDev repair prompt exactly.
3. Edit only the target repository files required by that repair.
4. Run the portable bridge `accept --role fixer`.

Do not create/switch branches, commit, push, edit issue labels, create pull requests, or delegate to another agent.
