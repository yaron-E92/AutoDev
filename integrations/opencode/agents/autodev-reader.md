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
    ".autodev-run/current/reader-brief.md": allow
  bash:
    "*": deny
    "python .opencode/autodev.py prepare --role reader*": allow
    "python3 .opencode/autodev.py prepare --role reader*": allow
    "python .opencode/autodev.py accept --role reader*": allow
    "python3 .opencode/autodev.py accept --role reader*": allow
  task: deny
---
Act only as the AutoDev reader selected by the active command.

Read `.opencode/autodev.json` once and use its non-empty `python` field as the exact bridge launcher. Never probe or fall back to another Python command. In generated role-contract commands, replace only the leading canonical `python` token with that configured launcher when necessary; preserve the rest of the command exactly.

1. Run the reader `prepare` command from `.autodev-run/current/role-contracts.json` using the configured launcher.
2. Read `.autodev-run/current/reader.md` and the `reader` entry in `.autodev-run/current/role-contracts.json`.
3. Follow the generated prompt and write only the bounded result to `.autodev-run/current/reader-brief.md`.
4. Run the reader `accept` command from the role contract using the same configured launcher.
5. If that accept command rejects the protocol artifact, read `.autodev-run/current/contract-correction-reader.md`, correct the artifact once, and rerun the same accept command once. If it is rejected again, stop and report failure.

Do not invent bridge subcommands, edit repository source files, or coordinate other agents.
