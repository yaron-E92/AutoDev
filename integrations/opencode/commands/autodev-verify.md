---
description: Semantically verify one AutoDev issue in an isolated read-only context
agent: autodev-verifier
subtask: false
---
Use the installed portable AutoDev bridge for issue/task `$ARGUMENTS` and stop when the Verifier role itself is accepted or fails. This standalone command intentionally uses `subtask: false`; do not change it back to `subtask: true`, because it must not return into an unrelated primary coordinator continuation.

1. Read `.opencode/autodev.json` once and use its non-empty `python` field as the exact bridge launcher for every command below. Do not probe `python`/`python3`, fall back to another interpreter, use `cd`/shell wrappers, or construct absolute repository/bridge/artifact paths. In the canonical commands below, substitute only that configured launcher for the leading `python` token.
2. Run exactly `python .opencode/autodev.py prepare --role verifier --arguments "$ARGUMENTS"` with that launcher.
3. Read the literal repository-relative paths `.autodev-run/current/verifier.md`, `.autodev-run/current/verification-result.template.json`, and `.autodev-run/current/role-contracts.json`. Do not prepend the repository path or insert additional path components.
4. Preserve every pre-populated acceptance criterion exactly and write only parser-compatible semantic JSON to `.autodev-run/current/verification-result.json`.
5. Run exactly `python .opencode/autodev.py accept --role verifier --input .autodev-run/current/verification-result.json` with the same launcher. Do not claim success before this command succeeds.
6. If rejected, use `.autodev-run/current/contract-correction-verifier.md` for the single allowed protocol correction, then rerun that exact accept command once.
7. After successful accept, return only success and `.autodev-run/current/verification-result.json`, then stop. Do not run the semantic stage, launch a fixer, create/update a PR, or continue the issue-to-PR workflow from this standalone command.

Do not invent bridge subcommands, edit repository source files, or delegate to another agent.
