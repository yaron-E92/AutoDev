---
description: Apply one targeted AutoDev repair in an isolated fixer context
agent: autodev-fixer
subtask: false
---
Use the installed portable AutoDev bridge for issue/task `$ARGUMENTS` and stop when the Fixer role itself is accepted or fails. This standalone command intentionally uses `subtask: false`; do not change it back to `subtask: true`, because it must not return into an unrelated primary coordinator continuation.

1. Read `.opencode/autodev.json` once and use its non-empty `python` field as the exact bridge launcher for every command below. Do not probe `python`/`python3`, fall back to another interpreter, use `cd`/shell wrappers, or construct absolute repository/bridge/artifact paths. In the canonical commands below, substitute only that configured launcher for the leading `python` token.
2. Run exactly `python .opencode/autodev.py prepare --role fixer --arguments "$ARGUMENTS"` with that launcher. `$ARGUMENTS` must select the current run plus local, semantic, or ci repair context.
3. Read the literal repository-relative paths `.autodev-run/current/fixer.md` and `.autodev-run/current/role-contracts.json`.
4. Edit only the target repository files required by that repair.
5. Run exactly `python .opencode/autodev.py accept --role fixer` with the same launcher. Do not claim success before this command succeeds.
6. After successful accept, return only success, then stop. Do not rerun verification, commit, push, create/update a PR, or continue the issue-to-PR workflow from this standalone command.

Do not invent bridge subcommands, create/switch branches, commit, push, edit issue labels, create pull requests, or delegate to another agent.
