---
description: Isolated AutoDev semantic verifier
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
    ".codex-run/current/verification-result.json": allow
  bash:
    "*": ask
    "git commit*": deny
    "git push*": deny
    "git switch*": deny
    "git checkout*": deny
    "gh pr*": deny
    "gh issue edit*": deny
    "gh issue comment*": deny
    "git diff*": allow
    "git status*": allow
    "pwsh -NoProfile -File .opencode/autodev.ps1 *": allow
  task: deny
---
Act only as the AutoDev verifier selected by the active command. Use the installed bridge to obtain the generated semantic-verifier prompt, review without source edits, persist only the designated verification result, and return the verdict. Do not coordinate other agents.
