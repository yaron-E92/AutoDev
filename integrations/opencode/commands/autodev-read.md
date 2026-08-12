---
description: Prepare and run the isolated AutoDev repository reader
agent: autodev-reader
subtask: false
---
Use the installed portable AutoDev bridge for issue/task `$ARGUMENTS` and stop when the Reader role itself is accepted or fails. This standalone command intentionally uses `subtask: false`; do not change it back to `subtask: true`, because it must not return into an unrelated primary coordinator continuation.

1. Read `.opencode/autodev.json` once and use its non-empty `python` field as the exact bridge launcher for every command below. Do not probe `python`/`python3`, fall back to another interpreter, use `cd`/shell wrappers, or construct absolute repository/bridge/artifact paths. In the canonical commands below, substitute only that configured launcher for the leading `python` token.
2. Run exactly `python .opencode/autodev.py prepare --role reader --arguments "$ARGUMENTS"` with that launcher.
3. Read the literal repository-relative paths `.autodev-run/current/reader.md` and `.autodev-run/current/role-contracts.json`. Do not prepend the repository path or insert additional path components.
4. Write only the bounded factual reader brief to `.autodev-run/current/reader-brief.md`.
5. Run exactly `python .opencode/autodev.py accept --role reader --input .autodev-run/current/reader-brief.md` with the same launcher. Do not claim success before this command succeeds.
6. If rejected, use `.autodev-run/current/contract-correction-reader.md` for the single allowed protocol correction, then rerun that exact accept command once.
7. After successful accept, return only success and `.autodev-run/current/reader-brief.md`, then stop. Do not launch Synthesizer, run resume/status/role-check, or continue the issue-to-PR workflow from this standalone command.

Do not invent bridge subcommands, delegate to another agent, or edit repository source files.
