# Windows Scripts

Windows PowerShell automation scripts belong here.

Common files remain at the repository root:

- `agentFiles/`
- `promptTemplates/`
- `skill/`
- `codex-profiles.json`

Do not duplicate common prompt, profile, or skill files under this directory.
## Single-command workflow

Use `issue-to-pr-cycle.ps1` for the native Windows equivalent of the old prompt-driven flow. It composes the Windows prepare/finalize/mark scripts and invokes configurable planner and coder agent commands or raw provider/model runners at the points where agent work is needed.

```powershell
pwsh -File "C:\Users\you\codex-tools\issue-to-pr-cycle.ps1" `
  -Mode Run `
  -Username owner `
  -Repo AutoDev `
  -PlannerAgentCommand "reader-agent {prompt_file}" `
  -AgentCommand "coder-agent {prompt_file}"
```

For provider mode, pass `-PlannerProvider command` / `-AgentProvider command` with command strings when the tool returns stdout artifacts, or pass model names for Ollama. Supplying `-PlannerModel` or `-AgentModel` without an explicit provider defaults that side to `ollama` and runs `ollama run <model>` with the prompt on stdin. Provider-mode implementation and repair responses must return `NO_CHANGES_REQUIRED` or a marked unified diff that AutoDev applies with `git apply`.

```powershell
pwsh -File "C:\Users\you\codex-tools\issue-to-pr-cycle.ps1" `
  -Mode Run `
  -Username owner `
  -Repo AutoDev `
  -PlannerModel qwen35-9b-32k `
  -AgentModel devstral-small2-12k
```

Omit `-Issue` to process the next open issue labeled `autodev:ready`, pass `-Issue 123` for a specific GitHub issue, or pass `-Description "..."` / `-DescriptionFile ideas.md` for a local task description. `-Mode Plan` runs only prepare plus the planner agent; individual transition modes are available for debugging and resuming.

## Planner-only helper

Use `codex-plan-current-issue.ps1` after `codex-prepare-next-ready-issue.ps1` when the planning phase should be run by a dedicated reader/planner agent before implementation starts. It reads `.codex-run/current/planner.md`, invokes the configured planner agent, and requires `.codex-run/current/plan.md` to be written.

```powershell
pwsh -File "C:\Users\you\codex-tools\codex-plan-current-issue.ps1" `
  -PlannerAgentCommand "reader-agent {prompt_file}"
```

If `-PlannerAgentCommand` is omitted, the script uses `$env:PLANNER_AGENT_COMMAND` or falls back to `codex exec`. Commands can use `{prompt_file}` or `{prompt}` placeholders for area-reader-style prompt runners and are executed through PowerShell, not bash.

## Automation Prompt

Use this as the Windows automation task, adjusting repo names and paths.

```text
Use the issue-to-pr-automation skill.

Run exactly one issue-to-PR cycle.

Important:
- Do not call `codex exec`.
- You are the Codex agent. Do the planning, implementation, repair, and verification yourself.
- Never merge to main.
- Process only one issue.
- Keep all changes issue-scoped.
- Do not perform unrelated refactors.

Step 1 — Prepare

Run:

pwsh -File "C:\Users\yaref92\codex-tools\codex-prepare-next-ready-issue.ps1" -Username "yaron-E92" -Repo "PHOODAB" -Base main -Remote origin -KeePassCliPath "C:\Program Files\KeePassXC\keepassxc-cli.exe" -KeePassDatabasePath "C:\Users\yaref92\Documents\CodexSecrets\codex-automation.kdbx" -KeePassKeyFilePath "C:\Users\yaref92\Documents\CodexSecretsKey\codex-automation.keyx" -KeePassEntryPath "PHOODAB_CODEX_EXPIRES28MAY2027" -KeePassNoPassword -GhConfigDir ".codex-run\gh-config"

If it prints NO_READY_ISSUE, stop.

Step 2 — Plan

Read `.codex-run/current/planner.md`.

Write the plan to:

.codex-run/current/plan.md

Step 3 — Render implementer prompt

Run:

pwsh -File "C:\Users\yaref92\codex-tools\codex-finalize-current-issue.ps1" -Mode RenderImplementerPrompt

Step 4 — Implement

Read `.codex-run/current/implementer.md`.

Implement the issue directly in the workspace.

After implementing the issue, write a concise Git commit message to:

.codex-run/current/commit-message.txt

Rules for the commit message:
- One short first line.
- Prefer imperative mood.
- Mention the affected area or behavior.
- Do not include markdown.
- Do not include quotes around the message.

Example:
Show item names in expiring entry lists

Step 5 — Local check

Run:

pwsh -File "C:\Users\yaref92\codex-tools\codex-finalize-current-issue.ps1" -Mode LocalCheck

If it prints LOCAL_CHECK_FAILED:
- Read `.codex-run/current/local-repair.md`.
- Fix only the local-check failure.
- Rerun LocalCheck.
- Repeat at most 3 times.

Step 6 — PR and CI

Run:

pwsh -File "C:\Users\yaref92\codex-tools\codex-finalize-current-issue.ps1" -Mode PrAndCi

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

pwsh -File "C:\Users\yaref92\codex-tools\codex-finalize-current-issue.ps1" -Mode RenderVerificationRepair

- Read `.codex-run/current/verification-repair.md`.
- Fix only the verifier gaps.
- Rerun LocalCheck.
- Rerun PrAndCi.
- Verify again.

Step 8 — Mark ready

When verification passes, run:

pwsh -File "C:\Users\yaref92\codex-tools\codex-mark-current-issue.ps1" -Status ReadyForReview

If you must give up, run:

pwsh -File "C:\Users\yaref92\codex-tools\codex-mark-current-issue.ps1" -Status Blocked -Message "Automation could not complete after repair attempts."

Rules:
- Never merge to main.
- Do not bypass the trusted scripts for GitHub state changes.
- Do not perform unrelated refactors.
- Do not expand the issue scope.
```
