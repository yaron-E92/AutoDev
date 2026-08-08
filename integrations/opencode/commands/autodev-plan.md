---
description: Plan one AutoDev issue in an isolated planner context
agent: autodev-planner
subtask: true
---
Use the installed portable AutoDev bridge for issue/task `$ARGUMENTS`.

1. Run `python .opencode/autodev.py prepare --role planner --arguments "$ARGUMENTS"` (use `python3` instead where that is the available Python command).
2. Read `.codex-run/current/planner.md` and follow that generated AutoDev prompt exactly.
3. Write only the final plan to `.codex-run/current/plan.md`.
4. Run the portable bridge `accept --role planner --input .codex-run/current/plan.md`.

Do not delegate to another agent and do not edit repository source files.
