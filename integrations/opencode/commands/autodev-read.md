---
description: Prepare and run the isolated AutoDev repository reader
agent: autodev-reader
subtask: true
---
Use the installed portable AutoDev bridge for issue/task `$ARGUMENTS`.

1. Run `python .opencode/autodev.py prepare --role reader --arguments "$ARGUMENTS"` (use `python3` instead where that is the available Python command).
2. Read `.codex-run/current/reader.md` and follow that generated AutoDev prompt exactly.
3. Write only the resulting factual reader brief to `.codex-run/current/reader-brief.md`.
4. Run the portable bridge `accept --role reader --input .codex-run/current/reader-brief.md`.

Do not delegate to another agent and do not edit repository source files.
