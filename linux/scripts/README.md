# Linux Scripts

Linux-specific automation scripts belong here.

Common files remain at the repository root:

- `agentFiles/`
- `promptTemplates/`
- `skill/`
- `codex-profiles.json`

Do not duplicate common prompt, profile, or skill files under this directory.

## Automation Prompt

Use this as the Linux automation task, adjusting repo names and paths.

This prompt assumes the Linux automation scripts are installed under `~/automation/scripts`, and the target project has an environment file under `~/automation/state`.

```text
Use the issue-to-pr-automation skill.

Run exactly one issue-to-PR cycle in this Linux project.

Important:
- Do not call `codex exec`.
- You are the Codex agent. Do the planning, implementation, repair, and verification yourself.
- Never merge to main.
- Process only one issue.
- Keep all changes issue-scoped.
- Do not perform unrelated refactors.
- Do not use local git commands that write `.git` metadata.
- Use only the trusted automation scripts for GitHub issue/PR/CI state changes.

Environment file:

~/automation/state/PROJECT.env

Step 1 — Prepare

Run:

~/automation/scripts/with-env.sh ~/automation/state/PROJECT.env ~/automation/scripts/prepare-next-ready-issue.sh --owner OWNER --repo REPO --base main --remote origin

If it prints NO_READY_ISSUE, stop.

Step 2 — Plan

Read:

.codex-run/current/planner.md

Write the plan to:

.codex-run/current/plan.md

Step 3 — Render implementer prompt

Run:

~/automation/scripts/with-env.sh ~/automation/state/PROJECT.env ~/automation/scripts/finalize-current-issue.sh --mode RenderImplementerPrompt

Step 4 — Implement

Read:

.codex-run/current/implementer.md

Implement the issue directly in the workspace.

Also write a concise commit message to:

.codex-run/current/commit-message.txt

Commit message rules:
- One short first line.
- Imperative mood.
- Mention the affected behavior or area.
- No markdown.
- No quotes around the message.

Step 5 — Local check

Run:

~/automation/scripts/with-env.sh ~/automation/state/PROJECT.env ~/automation/scripts/finalize-current-issue.sh --mode LocalCheck

If it prints LOCAL_CHECK_FAILED:
- Read `.codex-run/current/local-repair.md`.
- Fix only the local-check failure.
- Rerun LocalCheck.
- Repeat at most 3 times.

Step 6 — PR and CI

Run:

~/automation/scripts/with-env.sh ~/automation/state/PROJECT.env ~/automation/scripts/finalize-current-issue.sh --mode PrAndCi

If it prints CI_FAILED:
- Read `.codex-run/current/ci-repair.md`.
- Fix only the CI failure.
- Run LocalCheck again.
- Then rerun PrAndCi.
- Repeat at most 3 times.

Step 7 — Verify

When PrAndCi prints CI_PASSED:
- Read `.codex-run/current/verifier.md`.
- Verify whether the implementation fully satisfies the issue.
- If it passes, write exactly `PASS` to `.codex-run/current/verification-result.md`.
- If it fails, write `FAIL` plus the concrete gaps to `.codex-run/current/verification-result.md`.

If verification fails:
- Run:

~/automation/scripts/with-env.sh ~/automation/state/PROJECT.env ~/automation/scripts/finalize-current-issue.sh --mode RenderVerificationRepair

- Read `.codex-run/current/verification-repair.md`.
- Fix only the verifier gaps.
- Rerun LocalCheck.
- Rerun PrAndCi.
- Verify again.

Step 8 — Mark ready

When verification passes, run:

~/automation/scripts/with-env.sh ~/automation/state/PROJECT.env ~/automation/scripts/mark-current-issue.sh --status ReadyForReview

If you must give up, run:

~/automation/scripts/with-env.sh ~/automation/state/PROJECT.env ~/automation/scripts/mark-current-issue.sh --status Blocked --message "Automation could not complete after repair attempts."

Rules:
- Never merge to main.
- Do not bypass the trusted scripts for GitHub state changes.
- Do not perform unrelated refactors.
- Do not expand the issue scope.
```
