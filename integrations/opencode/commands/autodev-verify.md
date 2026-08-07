---
description: Semantically verify one AutoDev issue in an isolated read-only context
agent: autodev-verifier
subtask: true
---
Use the installed AutoDev bridge for issue/task `$ARGUMENTS`.

1. Run `pwsh -NoProfile -File .opencode/autodev.ps1 prepare --role verifier --arguments "$ARGUMENTS"`.
2. Read `.codex-run/current/verifier.md` and follow that generated AutoDev prompt exactly.
3. Write only the required semantic JSON to `.codex-run/current/verification-result.json`.
4. Run `pwsh -NoProfile -File .opencode/autodev.ps1 accept --role verifier --input .codex-run/current/verification-result.json`.

Do not edit repository source files and do not delegate to another agent.
