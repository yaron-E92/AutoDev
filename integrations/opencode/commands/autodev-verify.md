---
description: Semantically verify one AutoDev issue in an isolated read-only context
agent: autodev-verifier
subtask: true
---
Use the installed portable AutoDev bridge for issue/task `$ARGUMENTS`.

1. Run exactly `python .opencode/autodev.py prepare --role verifier --arguments "$ARGUMENTS"` (use `python3` instead only when that is the available Python command).
2. Read `.autodev-run/current/verifier.md`, `.autodev-run/current/verification-result.template.json`, and the generated verifier contract.
3. Preserve every pre-populated acceptance criterion exactly and write only parser-compatible semantic JSON to `.autodev-run/current/verification-result.json`.
4. Run exactly `python .opencode/autodev.py accept --role verifier --input .autodev-run/current/verification-result.json`.
5. If rejected, use `.autodev-run/current/contract-correction-verifier.md` for the single allowed protocol correction, then rerun that exact accept command once.

Do not invent bridge subcommands, edit repository source files, or delegate to another agent.
