---
description: Prepare and run the isolated AutoDev repository reader
agent: autodev-reader
subtask: true
---
Use the installed portable AutoDev bridge for issue/task `$ARGUMENTS`.

1. Run exactly `python .opencode/autodev.py prepare --role reader --arguments "$ARGUMENTS"` (use `python3` instead only when that is the available Python command).
2. Read `.autodev-run/current/reader.md` and the generated reader contract in `.autodev-run/current/role-contracts.json`.
3. Write only the bounded factual reader brief to `.autodev-run/current/reader-brief.md`.
4. Run exactly `python .opencode/autodev.py accept --role reader --input .autodev-run/current/reader-brief.md`.
5. If rejected, use `.autodev-run/current/contract-correction-reader.md` for the single allowed protocol correction, then rerun that exact accept command once.

Do not invent bridge subcommands, delegate to another agent, or edit repository source files.
