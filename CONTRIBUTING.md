# Contributing

Thank you for improving this automation. The goal is simple:

> Let Codex Desktop process small GitHub issues into reviewable PRs without giving it broad machine access, without nested `codex exec`, and without extra OpenAI API billing.

This project is intentionally conservative. It should feel boring in the good way: predictable, auditable, and easy to recover when the robot trips over a rake.

---

## Design principles

### 1. Codex Desktop is the AI brain

Do not reintroduce nested calls like:

```powershell
codex exec ...
```

The final working design avoids nested Codex CLI calls because they caused transport/proxy issues and would require a different auth/billing path.

Codex Desktop should do:

```text
planning
implementation
repair
verification
```

The scripts should do deterministic operations only.

---

### 2. Do not write local `.git`

The Codex Desktop sandbox may block writes to `.git`, including:

```text
.git/FETCH_HEAD
.git/index
.git/refs
.git/worktrees/...
```

Do not add local Git operations that require metadata writes:

```powershell
git fetch
git switch
git checkout
git add
git commit
git push
```

The final design commits through the GitHub API instead.

Allowed Git usage, if ever needed, should be read-only and carefully justified. Prefer `gh api` over local Git.

---

### 3. GitHub state changes go through trusted scripts

The automation prompt should not ask Codex to manually mutate GitHub issue/PR state except by running trusted scripts.

Trusted scripts:

```text
codex-prepare-next-ready-issue.ps1
codex-finalize-current-issue.ps1
codex-mark-current-issue.ps1
ensure-codex-labels.ps1
```

---

### 4. One issue per run

The automation should process exactly one issue per cycle.

Good:

```text
one issue
one branch
one PR
one verification loop
```

Bad:

```text
batching issues
multi-issue mega PRs
background branch soup
```

Branch soup tastes like regret.

---

### 5. Keep repos clean

Each target repo should need only:

```text
AGENTS.md
```

Do not require repo-local verifier scripts unless there is a strong reason.

Global reusable tooling belongs under:

- Windows:

```text
C:\Users\<you>\codex-tools
```

Repository source files are split by portability:

```text
agentFiles/
promptTemplates/
skill/
codex-profiles.json
```

stay common at the root. OS-specific source scripts belong under:

```text
windows/scripts/
linux/scripts/
```

---

## Labels

Required workflow labels:

```text
codex:ready
codex:in-progress
codex:ready-for-review
codex:blocked
```

Optional area labels:

```text
area:backend
area:web
area:maui
area:python
```

Area labels are flags and may be combined.

Do not add:

```text
area:fullstack
```

Use combinations instead:

```text
area:backend + area:web
area:backend + area:maui
area:web + area:maui
```

---

## Script responsibilities

### `codex-prepare-next-ready-issue.ps1`

Allowed responsibilities:

- load GitHub token
- set `GH_TOKEN`
- set `GH_CONFIG_DIR`
- select one ready issue
- mark issue `codex:in-progress`
- resolve profiles
- read base commit/tree from GitHub API
- create `.codex-run/current/state.json`
- create `.codex-run/current/workspace-snapshot.json`
- render `.codex-run/current/planner.md`

Not allowed:

- nested `codex exec`
- local `git fetch`
- local branch creation
- local commit or push

---

### `codex-finalize-current-issue.ps1`

Allowed modes:

```text
RenderImplementerPrompt
LocalCheck
PrAndCi
RenderVerificationRepair
```

Allowed responsibilities:

- render implementer/repair/verifier prompts
- run configured local check
- compare workspace against snapshot
- create blobs/trees/commits through GitHub API
- create/update remote branch ref through GitHub API
- create PR
- watch CI
- write CI summary
- render verifier prompt from PR diff

Not allowed:

- local Git commit/push
- direct Codex CLI invocation
- merging PRs

---

### `codex-mark-current-issue.ps1`

Allowed responsibilities:

- mark issue ready for review
- mark issue blocked
- add issue comments
- update `.codex-run/current/state.json`

Not allowed:

- implementation changes
- PR merging

---

## Authentication

Recommended GitHub auth:

```text
fine-grained PAT
selected repositories only
stored in dedicated KeePassXC database
loaded through keepassxc-cli
```

Recommended token permissions:

```text
Metadata: read
Contents: read/write
Issues: read/write
Pull requests: read/write
Actions: read
```

The KeePassXC database should contain automation tokens only.

Do not store:

```text
personal passwords
GitHub account password
recovery codes
email credentials
```

---

## Local checks

Checks are selected through `codex-profiles.json`.

Examples:

```text
area:web
  -> codex-verify.ps1 -Profiles web

area:backend + area:web
  -> codex-verify.ps1 -Profiles backend,web

no area label
  -> codex-verify.ps1 -Profiles auto
```

`codex-verify.ps1` should remain global and generic.

For .NET/MAUI repos:

- backend verification should avoid accidentally building MAUI when possible
- MAUI verification should target MAUI `.csproj` files directly
- a non-GUI `.slnf` may be preferred for non-MAUI .NET verification

---

## Prompt templates

Prompt templates live in:

```text
codex-tools/prompts/
```

Current templates:

```text
planner.md
implementer.md
local-repair.md
ci-repair.md
verifier.md
verification-repair.md
```

Templates should stay role-specific and concise.

Do not make the scripts generate large prompt prose directly. Scripts should pass values into templates.

---

## Testing changes

Before testing with a real issue:

1. Use a small repo or a harmless issue.
2. Ensure labels exist.
3. Ensure KeePass token loads.
4. Ensure `gh issue list` works.
5. Run the prepare script.
6. Confirm `.codex-run/current/state.json` and `planner.md` are created.
7. Let Codex implement a tiny change.
8. Run `LocalCheck`.
9. Run `PrAndCi`.
10. Confirm a PR is created.

Good smoke test issue:

```text
Update one README sentence.
```

Bad smoke test issue:

```text
Refactor authentication and migrate the database.
```

Do not start by asking the robot to juggle chainsaws in a fireworks factory.

---

## Recovery

Reset a blocked issue:

```powershell
gh issue edit 54 `
  --repo "OWNER/REPO" `
  --remove-label "codex:blocked" `
  --remove-label "codex:in-progress" `
  --add-label "codex:ready"
```

Mark current state blocked:

```powershell
pwsh -File "$env:USERPROFILE\codex-tools\codex-mark-current-issue.ps1" `
  -Status Blocked `
  -Message "Manual review needed."
```

Mark current state ready for review:

```powershell
pwsh -File "$env:USERPROFILE\codex-tools\codex-mark-current-issue.ps1" `
  -Status ReadyForReview
```

If `.codex-run/current` is stale, archive it manually or rerun prepare with the appropriate force/current handling.

---

## Pull request expectations

A successful PR should include:

- one issue-scoped change
- a clear title from the issue
- body containing issue text and plan
- local verification command
- passing CI, if required checks exist
- issue marked `codex:ready-for-review`

The automation must never merge the PR.

---

## What not to add back

Do not reintroduce these unless there is a major architecture change:

```text
nested codex exec
OpenAI API key requirement
local git fetch/switch/commit/push in the sandbox
area:fullstack label
repo-local verify scripts by default
broad sandbox full-access requirement
automatic merge
```

If a change needs one of those, document why and treat it as a design decision, not a casual patch.

---

## Tone of the project

Be strict with scope, kind to future maintainers, and suspicious of cleverness.

Boring automation is good automation.

Clever automation is often just a bug wearing sunglasses.
