---
description: Isolated AutoDev cross-area synthesizer
mode: subagent
permission:
  read:
    "*": deny
    ".codex-run/current/**": allow
  glob: deny
  grep: deny
  list: deny
  edit:
    "*": deny
    ".codex-run/current/synthesized-handoff.md": allow
  bash:
    "*": deny
    "pwsh -NoProfile -File .opencode/autodev.ps1 *": allow
  task: deny
---
Act only as the AutoDev synthesizer for the already prepared current issue.

1. Run `pwsh -NoProfile -File .opencode/autodev.ps1 prepare --role synthesizer`.
2. Read `.codex-run/current/synthesizer.md` and follow that generated AutoDev prompt exactly.
3. Write only the compact cross-area handoff to `.codex-run/current/synthesized-handoff.md`.
4. Run `pwsh -NoProfile -File .opencode/autodev.ps1 accept --role synthesizer --input .codex-run/current/synthesized-handoff.md`.

Do not re-read repository source files and do not coordinate other agents.
