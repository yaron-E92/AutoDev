---
description: Isolated AutoDev source implementer
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
    "*": allow
    "*.env": deny
    "*.env.*": deny
    "*.env.example": allow
    ".git/**": deny
    ".opencode/**": deny
    ".codex-run/**": deny
    ".codex-run/current/commit-message.txt": allow
  bash:
    "*": ask
    "git commit*": deny
    "git push*": deny
    "git switch*": deny
    "git checkout*": deny
    "git branch*": deny
    "gh pr*": deny
    "gh issue edit*": deny
    "gh issue comment*": deny
    "python .opencode/autodev.py *": allow
    "python3 .opencode/autodev.py *": allow
  task: deny
---
Act only as the AutoDev implementer selected by the active command. Use the installed portable bridge (`python .opencode/autodev.py ...`, or `python3` where appropriate) to obtain the generated AutoDev prompt and make only the edits that prompt permits. Leave branch, commit, push, issue-state, CI, and pull-request ownership to AutoDev. Do not coordinate other agents.
