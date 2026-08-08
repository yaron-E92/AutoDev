---
description: Apply one targeted AutoDev repair in an isolated fixer context
agent: autodev-fixer
subtask: true
---
Use the installed portable AutoDev bridge for issue/task `$ARGUMENTS`.

1. Run exactly `python .opencode/autodev.py prepare --role fixer --arguments "$ARGUMENTS"` (use `python3` instead only when that is the available Python command; `$ARGUMENTS` must select the issue/current run plus local, semantic, or ci repair context).
2. Read `.autodev-run/current/fixer.md` and the generated fixer contract.
3. Edit only the target repository files required by that repair.
4. Run exactly `python .opencode/autodev.py accept --role fixer`.

Do not invent bridge subcommands, create/switch branches, commit, push, edit issue labels, create pull requests, or delegate to another agent.
