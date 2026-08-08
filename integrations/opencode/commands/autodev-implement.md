---
description: Implement one prepared AutoDev issue in an isolated editor context
agent: autodev-implementer
subtask: true
---
Use the installed portable AutoDev bridge for issue/task `$ARGUMENTS`.

1. Run exactly `python .opencode/autodev.py prepare --role implementer --arguments "$ARGUMENTS"` (use `python3` instead only when that is the available Python command).
2. Read `.codex-run/current/implementer.md` and the generated implementer contract.
3. Edit only the target repository files required by that prompt.
4. Write one concise commit-message line to `.codex-run/current/commit-message.txt`.
5. Run exactly `python .opencode/autodev.py accept --role implementer`.
6. If rejected, use `.codex-run/current/contract-correction-implementer.md` for the single allowed protocol correction, then rerun that exact accept command once.

Do not invent bridge subcommands, create/switch branches, commit, push, edit issue labels, create pull requests, or delegate to another agent.
