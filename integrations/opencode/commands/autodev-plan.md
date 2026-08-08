---
description: Plan one AutoDev issue in an isolated planner context
agent: autodev-planner
subtask: true
---
Use the installed portable AutoDev bridge for issue/task `$ARGUMENTS`.

1. Run exactly `python .opencode/autodev.py prepare --role planner --arguments "$ARGUMENTS"` (use `python3` instead only when that is the available Python command).
2. Read `.autodev-run/current/planner.md`, `.autodev-run/current/plan.template.md`, and the generated planner contract.
3. Write only the final six-section plan to `.autodev-run/current/plan.md` using the template structure exactly.
4. Run exactly `python .opencode/autodev.py accept --role planner --input .autodev-run/current/plan.md`.
5. If rejected, use `.autodev-run/current/contract-correction-planner.md` for the single allowed protocol correction, then rerun that exact accept command once.

Do not invent bridge subcommands, delegate to another agent, or edit repository source files.
