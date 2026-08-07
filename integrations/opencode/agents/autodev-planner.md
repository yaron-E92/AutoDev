---
description: Isolated AutoDev implementation planner
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
    ".codex-run/current/plan.md": allow
  bash:
    "*": deny
    "python .opencode/autodev.py *": allow
    "python3 .opencode/autodev.py *": allow
  task: deny
---
Act only as the AutoDev planner selected by the active command. Use the installed portable bridge (`python .opencode/autodev.py ...`, or `python3` where appropriate) to obtain the generated AutoDev prompt, follow that prompt, persist only the designated plan artifact, and return the final plan. Do not coordinate other agents or edit repository source files.
