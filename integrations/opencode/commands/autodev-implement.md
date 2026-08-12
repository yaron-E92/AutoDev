---
description: Implement one prepared AutoDev issue in an isolated editor context
agent: autodev-implementer
subtask: false
---
Use the installed portable AutoDev bridge for issue/task `$ARGUMENTS` and stop when the Implementer role itself is accepted or fails. This standalone command intentionally uses `subtask: false`; do not change it back to `subtask: true`, because it must not return into an unrelated primary coordinator continuation.

1. Read `.opencode/autodev.json` once and use its non-empty `python` field as the exact bridge launcher for every command below. Do not probe `python`/`python3`, fall back to another interpreter, use `cd`/shell wrappers, or construct absolute repository/bridge/artifact paths. In the canonical commands below, substitute only that configured launcher for the leading `python` token.
2. Run exactly `python .opencode/autodev.py prepare --role implementer --arguments "$ARGUMENTS"` with that launcher.
3. Read the literal repository-relative paths `.autodev-run/current/implementer.md` and `.autodev-run/current/role-contracts.json`.
4. Edit only the target repository files required by that prompt.
5. Write one concise commit-message line to `.autodev-run/current/commit-message.txt`.
6. Run exactly `python .opencode/autodev.py accept --role implementer` with the same launcher. Do not claim success before this command succeeds.
7. If rejected, use `.autodev-run/current/contract-correction-implementer.md` for the single allowed protocol correction, then rerun that exact accept command once.
8. After successful accept, return only success and `.autodev-run/current/commit-message.txt`, then stop. Do not launch verification, commit, push, create/update a PR, or continue the issue-to-PR workflow from this standalone command.

Do not invent bridge subcommands, create/switch branches, commit, push, edit issue labels, create pull requests, or delegate to another agent.
