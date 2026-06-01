# Codex Issue-to-PR Automation

A local, user-level automation setup that lets the **Codex desktop app** process GitHub issues into pull requests.

The final architecture intentionally avoids the traps we hit along the way:

- no nested `codex exec`
- no OpenAI API key
- no extra API billing beyond your Codex/ChatGPT subscription path
- no local `git fetch`, `git switch`, `git commit`, or `git push` inside the Codex sandbox
- no writing to `.git`
- no repo-specific verifier scripts

Instead, Codex Desktop acts as the AI agent, while PowerShell scripts handle deterministic state, GitHub issue/PR operations, local checks, and remote commit creation through the GitHub API.

```text
GitHub issue
  -> prepare script
  -> Codex desktop plans
  -> Codex desktop implements
  -> finalize script runs local checks
  -> finalize script commits through GitHub API
  -> finalize script creates PR and watches CI
  -> Codex desktop verifies
  -> mark script updates issue labels/comments
```

The repo gets only one required local file:

```text
AGENTS.md
```

Everything else lives globally under:

```text
C:\Users\<you>\codex-tools
```

---

## 1. What this solves

This setup automates the boring issue-to-PR loop:

1. Pick one GitHub issue labeled `codex:ready`.
2. Mark it `codex:in-progress`.
3. Resolve profiles from area labels such as `area:backend`, `area:web`, `area:maui`, or `area:python`.
4. Render planner/implementer/repair/verifier prompts.
5. Let Codex Desktop do the AI work.
6. Run local verification.
7. If needed, let Codex repair local failures.
8. Create a remote commit using the GitHub API.
9. Create a PR.
10. Watch required CI checks.
11. If needed, let Codex repair CI failures.
12. Verify the implementation against the issue.
13. Mark the issue `codex:ready-for-review`.

It never merges to `main`. The human remains merge authority. Crown stays on head; robot gets a broom.

---

## 2. Why GitHub API commit mode is used

Codex Desktop's sandbox protects `.git` metadata. That means normal Git commands such as these can fail inside the automation environment:

```powershell
git fetch origin main
git switch -c codex/issue-123
git commit -m "..."
git push
```

The failure usually looks like:

```text
Permission denied: .git/FETCH_HEAD
```

The final setup avoids that entire class of bugs. Codex edits workspace files, and the finalize script creates the commit remotely using the GitHub API:

```text
changed files
  -> GitHub blobs
  -> GitHub tree
  -> GitHub commit
  -> GitHub branch ref
  -> PR
```

No `.git` writes. No sandbox knife-fight.

---

## 3. Prerequisites

Install and verify:

```powershell
git --version
gh --version
pwsh --version
```

You also need:

- Codex Desktop
- KeePassXC
- `keepassxc-cli.exe`
- a fine-grained GitHub PAT stored in a dedicated KeePassXC database

Recommended KeePassXC setup:

```text
Database:
  codex-automation.kdbx

Key file:
  codex-automation.keyx

Entry:
  PHOODAB_CODEX_EXPIRES28MAY2027

Entry password field:
  github_pat_...
```

The database should contain **only automation PATs**, not personal passwords.

Recommended fine-grained PAT permissions:

```text
Repository access:
  selected repositories only

Repository permissions:
  Metadata: read
  Contents: read/write
  Issues: read/write
  Pull requests: read/write
  Actions: read
```

Use branch protection on `main`.

---

## 4. Global folder layout

Create:

```powershell
New-Item -ItemType Directory -Force -Path "$env:USERPROFILE\codex-tools"
New-Item -ItemType Directory -Force -Path "$env:USERPROFILE\codex-tools\prompts"
New-Item -ItemType Directory -Force -Path "$env:USERPROFILE\.agents\skills\issue-to-pr-automation"
```

Final global layout:

```text
C:\Users\<you>\codex-tools\
  codex-common.ps1
  codex-prepare-next-ready-issue.ps1
  codex-finalize-current-issue.ps1
  codex-mark-current-issue.ps1
  codex-verify.ps1
  codex-profiles.json
  ensure-codex-labels.ps1
  prompts\
    planner.md
    implementer.md
    local-repair.md
    ci-repair.md
    verifier.md
    verification-repair.md

C:\Users\<you>\.agents\skills\issue-to-pr-automation\
  SKILL.md
```

Optional global Codex instructions:

```text
C:\Users\<you>\.codex\AGENTS.md
```

Keep global rules general. Repo-specific facts belong in the repo's `AGENTS.md`.

---

## 5. Repository layout

Each repo should contain:

```text
AGENTS.md
```

That file explains the repo structure, architecture boundaries, and area label meaning.

Example area labels:

```text
area:backend
area:web
area:maui
area:python
```

Labels are flags and may be combined:

```text
area:backend + area:web
area:backend + area:maui
area:web + area:maui
area:backend + area:web + area:maui
```

Do **not** create `area:fullstack`. Full-stack means multiple area labels.

---

## 6. GitHub labels

Each target repo should have:

```text
codex:ready
codex:in-progress
codex:ready-for-review
codex:blocked

area:backend
area:web
area:maui
area:python
```

Use the label script:

```powershell
pwsh -File "$env:USERPROFILE\codex-tools\ensure-codex-labels.ps1" `
  -Username "yaron-E92" `
  -Repo "PHOODAB" `
  -IncludeAreaLabels
```

---

## 7. `codex-profiles.json`

Create:

```text
C:\Users\<you>\codex-tools\codex-profiles.json
```

Recommended content:

```json
{
  "defaultProfile": "auto",
  "verifyCommandTemplate": "pwsh -File \"{{CodexToolsDir}}\\codex-verify.ps1\" -Profiles \"{{ProfilesCsv}}\"",
  "profiles": {
    "backend": {
      "labels": ["area:backend"],
      "verifyProfile": "backend",
      "stackContext": "- Backend/API area\n- Use repository AGENTS.md for architecture boundaries\n- Do not touch Web, MAUI, Python, or unrelated presentation layers unless explicitly required"
    },
    "web": {
      "labels": ["area:web"],
      "verifyProfile": "web",
      "stackContext": "- React + TypeScript + Vite web presentation layer\n- Use repository AGENTS.md for architecture boundaries\n- Do not touch Backend, MAUI, or Python unless explicitly required"
    },
    "maui": {
      "labels": ["area:maui"],
      "verifyProfile": "maui",
      "stackContext": "- .NET MAUI presentation layer\n- Use repository AGENTS.md for architecture boundaries\n- Do not touch Backend, persistence, domain logic, Web, or Python unless explicitly required"
    },
    "python": {
      "labels": ["area:python"],
      "verifyProfile": "python",
      "stackContext": "- Python area\n- Use repository AGENTS.md for architecture boundaries\n- Do not touch Backend, Web, or MAUI unless explicitly required"
    }
  }
}
```

Precedence:

```text
explicit -LocalCheck / -StackContext
  beats
explicit -Profiles
  beats
issue area labels
  beats
defaultProfile auto
```

---

## 8. Prompt templates

Create these files:

```text
C:\Users\<you>\codex-tools\prompts\planner.md
C:\Users\<you>\codex-tools\prompts\implementer.md
C:\Users\<you>\codex-tools\prompts\local-repair.md
C:\Users\<you>\codex-tools\prompts\ci-repair.md
C:\Users\<you>\codex-tools\prompts\verifier.md
C:\Users\<you>\codex-tools\prompts\verification-repair.md
```

The scripts render these templates using placeholders such as:

```text
{{IssueText}}
{{Plan}}
{{LocalCheck}}
{{StackContext}}
{{FailureLog}}
{{CiSummary}}
{{Diff}}
{{VerificationFailure}}
```

The automation agent reads the rendered files under:

```text
.codex-run/current/
```

---

## 9. Final workflow scripts

The final workflow uses three scripts:

```text
codex-prepare-next-ready-issue.ps1
codex-finalize-current-issue.ps1
codex-mark-current-issue.ps1
```

### 9.1 Prepare script

The prepare script:

- loads GitHub PAT from KeePassXC
- sets `GH_TOKEN`
- sets `GH_CONFIG_DIR`
- selects one issue
- marks it `codex:in-progress`
- resolves profiles from labels
- reads base SHA/tree SHA from GitHub API
- snapshots workspace files
- renders `planner.md`
- writes `.codex-run/current/state.json`

It does **not** run `git fetch`.
It does **not** create a local branch.
It does **not** call `codex exec`.

### 9.2 Finalize script

The finalize script has modes:

```text
RenderImplementerPrompt
LocalCheck
PrAndCi
RenderVerificationRepair
```

`PrAndCi`:

- compares current workspace files to the snapshot
- creates GitHub blobs
- creates a GitHub tree
- creates a GitHub commit
- creates or updates the remote branch ref
- creates a PR
- watches CI
- renders verifier prompt

It does **not** use local `git commit` or `git push`.

### 9.3 Mark script

The mark script updates issue state:

```text
ReadyForReview
Blocked
```

It removes `codex:in-progress` and adds either:

```text
codex:ready-for-review
```

or:

```text
codex:blocked
```

---

## 10. Codex Desktop automation prompt

Use this as the automation task, adjusting repo names and paths.

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

---

## 11. Typical issue workflow

Create an issue:

```powershell
gh issue create `
  --repo "yaron-E92/PHOODAB" `
  --title "Show item names in expiring / expired entries list" `
  --body "..." `
  --label "codex:ready" `
  --label "area:web"
```

Or mark an existing issue:

```powershell
gh issue edit 54 `
  --repo "yaron-E92/PHOODAB" `
  --add-label "codex:ready" `
  --add-label "area:web"
```

Codex automation will process one ready issue per run.

---

## 12. Recovery commands

Reset a blocked issue:

```powershell
gh issue edit 54 `
  --repo "yaron-E92/PHOODAB" `
  --remove-label "codex:blocked" `
  --remove-label "codex:in-progress" `
  --add-label "codex:ready"
```

Mark manually ready for review:

```powershell
pwsh -File "$env:USERPROFILE\codex-tools\codex-mark-current-issue.ps1" `
  -Status ReadyForReview
```

Mark manually blocked:

```powershell
pwsh -File "$env:USERPROFILE\codex-tools\codex-mark-current-issue.ps1" `
  -Status Blocked `
  -Message "Manual review needed."
```

---

## 13. Known limitations

GitHub API commit mode is intentionally simple.

Limitations:

- Renames appear as delete + add.
- File mode is always committed as `100644`.
- Very large files should not be edited by this workflow.
- Binary files can technically be committed via base64 blobs, but this workflow is meant for code/text.
- Workspace should be reasonably aligned with the GitHub base branch.
- Local checks may still create ignored files such as `bin`, `obj`, `node_modules`, or build output.
- The workflow assumes one active `.codex-run/current` at a time.

Recommended issue types:

```text
good:
  small UI fixes
  localized bugs
  small tests
  docs
  simple backend/web/MAUI changes

avoid:
  migrations
  auth/security-critical changes
  huge refactors
  ambiguous product work
  binary-heavy changes
```

---

## 14. Mental model

```text
Codex Desktop:
  brain

PowerShell:
  conductor

GitHub API:
  commit engine

CI:
  judge

Human:
  merge authority
```

The robot does the boring loop. CI keeps the sword. You keep the crown.
