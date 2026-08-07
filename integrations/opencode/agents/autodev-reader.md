---
description: Isolated AutoDev repository reader
mode: subagent
permission:
  read:
    "*": allow
    "*.env": deny
    "*.env.*": deny
    "*.env.example": allow
  glob: allow
  grep: allow
  list: allow
  edit:
    "*": deny
    ".codex-run/current/reader-brief.md": allow
  bash:
    "*": deny
    "pwsh -NoProfile -File .opencode/autodev.ps1 *": allow
  task: deny
---
Act only as the AutoDev reader selected by the active command. Use the installed bridge to obtain the generated AutoDev prompt, follow that prompt, persist only the designated reader artifact, and return the concise result. Do not coordinate other agents.
