# OpenCode frontend

AutoDev's OpenCode integration is an optional frontend over the existing AutoDev workflow. OpenCode owns isolated model conversations; AutoDev remains the source of truth for issue preparation, `.codex-run/current` artifacts, deterministic verification, semantic-verification contracts, repair limits, commits, CI, pull requests, and issue status.

## Windows quick start

Install or sync the adapter from an AutoDev checkout:

```powershell
pwsh -File .\scripts\install-opencode.ps1 `
  -TargetRepository C:\source\repos\TARGET_REPOSITORY
```

Then launch OpenCode from the target repository:

```powershell
cd C:\source\repos\TARGET_REPOSITORY
opencode
```

The primary workflow is one command:

```text
/autodev-issue-to-pr 123
```

That command coordinates the normal AutoDev flow from GitHub issue number through a created PR ready for human review. AutoDev never merges the PR automatically.

The command finishes with exactly one top-level state:

```text
PR_READY
BLOCKED
FAILED
```

`PR_READY` means the existing AutoDev commit/PR/required-CI boundary succeeded and the issue was marked ready for human review. `BLOCKED` means an expected workflow gate such as deterministic verification, semantic verification, or CI exhausted its allowed repair path or explicitly blocked progress. `FAILED` means setup, provider/subagent execution, or an underlying stage failed unexpectedly.

On `BLOCKED` or `FAILED`, the coordinator reports the issue number, AutoDev branch, completed/failed stage, concise reason, artifact directory, whether the repository was modified, whether a commit or PR exists, the PR URL when available, and the recommended next action.

## What the one-command coordinator does

The installed command uses the primary `autodev-coordinator` agent. The coordinator does not implement or repair code itself. It owns only ordering and stage decisions:

```text
preflight
  -> existing AutoDev Prepare
  -> Task: isolated reader
  -> Task: isolated synthesizer
  -> Task: isolated planner
  -> existing RenderImplementerPrompt
  -> Task: isolated implementer
  -> existing LocalCheck
       -> repair: isolated fixer -> LocalCheck again
  -> Task: isolated semantic verifier
       -> repair: existing semantic repair artifact -> isolated fixer
                  -> LocalCheck -> semantic verifier again
       -> blocked: stop safely
  -> existing PrAndCi
       -> CI repair: existing ci-repair.md -> isolated fixer
                     -> LocalCheck -> semantic verifier -> PrAndCi again
  -> existing ReadyForReview
  -> PR_READY
```

The coordinator never calls the existing full `Run` mode because that mode may invoke AutoDev-configured model providers itself. Model-heavy work stays in OpenCode's isolated role subagents.

The coordinator uses the thin bridge stage API:

```powershell
pwsh -NoProfile -File .opencode\autodev.ps1 stage --name <stage>
```

The bridge returns compact JSON with outcomes such as `CONTINUE`, `REPAIR`, `BLOCKED`, `FAILED`, or `PR_READY`. It does not reproduce Git, GitHub, CI, repair, or semantic-verifier behavior. It invokes or reads the existing AutoDev boundaries and artifacts.

Repair attempts respect the same environment settings used by the Windows workflow:

```text
MAX_REPAIR_ATTEMPTS
MAX_SEMANTIC_REPAIR_ATTEMPTS
```

The normal defaults remain 3 deterministic/CI repairs and 1 semantic repair unless the operator configures them differently.

## Isolation and durable artifacts

Reader, synthesizer, planner, implementer, fixer, and verifier work runs in isolated OpenCode subagent contexts. Child role agents retain `task: deny`; only the primary coordinator can invoke the six allowlisted AutoDev roles.

The coordinator does not carry role transcripts forward. State passes through the existing bounded artifacts, including:

```text
.codex-run/current/issue.md
.codex-run/current/reader-brief.md
.codex-run/current/synthesized-handoff.md
.codex-run/current/plan.md
.codex-run/current/implementer.md
.codex-run/current/commit-message.txt
.codex-run/current/local-repair.md
.codex-run/current/verification-result.json
.codex-run/current/verification/semantic-attempt-*.json
.codex-run/current/verification/final-verdict.json
.codex-run/current/verification-repair.md
.codex-run/current/ci-repair.md
.codex-run/current/state.json
```

Reader/synthesizer results remain bounded before they become downstream handoffs. Planner output continues through AutoDev's existing six-section parser. Semantic JSON continues through the existing #35 schema and now preserves successive `semantic-attempt-N.json` artifacts across a repair cycle.

## Coordinator permissions

The coordinator is installed as a primary agent with:

```text
edit: deny
read: only the small current state/verifier-result artifacts
bash: installed AutoDev bridge plus safe git status/diff only
task: deny all, then allow only the six autodev-* role agents
```

It cannot directly edit source files. It cannot launch arbitrary subagents. It has no direct branch/commit/push/PR/issue mutation shell permissions and never performs a merge.

Implementer/fixer permissions remain unchanged from the role frontend: they may edit target source files but deny VCS/PR/issue mutation. Reader/planner/verifier remain read-oriented and child roles cannot recursively invoke Task.

## Installed target-repository files

The idempotent installer creates or refreshes only AutoDev-owned files:

```text
.opencode/
  autodev.json
  autodev.ps1
  commands/
    autodev-issue-to-pr.md
    autodev-read.md
    autodev-plan.md
    autodev-implement.md
    autodev-fix.md
    autodev-verify.md
  agents/
    autodev-coordinator.md
    autodev-reader.md
    autodev-synthesizer.md
    autodev-planner.md
    autodev-implementer.md
    autodev-fixer.md
    autodev-verifier.md
```

Running the installer again updates those named files and leaves unrelated `.opencode` commands, agents, and configuration untouched.

`autodev.json` contains only the local AutoDev checkout path and Python command. It contains no API keys. Because the AutoDev checkout path is machine-specific, keep `autodev.json` local unless all contributors intentionally use the same path. The command/agent Markdown files are safe to commit if a target repository wants the AutoDev commands to be discoverable for everyone.

To sync after updating AutoDev, rerun the same installer command.

## Prerequisites

The target repository must already be usable by the existing AutoDev Windows workflow. The coordinator preflight checks that the target is a Git worktree, the configured AutoDev workflow exists, and `pwsh`, `gh`, and `git` are available before issue preparation mutates workflow state.

The normal AutoDev GitHub authentication setup remains authoritative. The coordinator does not introduce another credential/provider abstraction.

## Provider and model selection

Checked-in OpenCode commands and agents do not declare a model. Provider/model selection stays in normal OpenCode user/project/session configuration.

Example mappings may be:

```text
reader/synthesizer/planner/verifier -> Groq
implementer/fixer                   -> OpenRouter free models
```

or local Ollama models for any/all roles. These are examples only; AutoDev workflow code does not hardcode them.

The coordinator itself does not invoke AutoDev's provider layer for role work, so an OpenCode-local provider/model override does not require workflow-code changes and cannot be silently replaced by a different AutoDev role provider.

## Ponytail and Headroom

AutoDev's #34 prompt-policy layer is applied when the bridge renders each role prompt. An external OpenCode Ponytail plugin is not required. If one is installed, configure or disable it for `autodev-*` agents so it does not inject a contradictory second policy.

Role isolation and the one-command coordinator do not require Headroom. #36 remains the optional AutoDev provider-side Headroom implementation for provider-backed workflows; the OpenCode coordinator does not add another Headroom routing path.

## Advanced/manual role controls

The individual commands remain available for debugging or intentional intervention:

```text
/autodev-read 123
/autodev-plan 123
/autodev-implement 123
/autodev-fix 123
/autodev-verify 123
```

Those five commands continue to use `subtask: true`, so each role runs in an isolated OpenCode context. The synthesizer remains available as the `autodev-synthesizer` subagent.

## Existing workflows remain independent

OpenCode is still optional. These existing entrypoints remain independently usable and do not depend on the OpenCode adapter:

```text
scripts/run-real-issue.ps1
windows/scripts/issue-to-pr-cycle.ps1
automation.prompt_runner
automation.run_real_issue
```

The coordinator does not implement #37 resumability, #36 Headroom, or #38 evaluation. It also does not add an OpenCode-only Git/PR/CI engine: `Prepare`, `RenderImplementerPrompt`, `LocalCheck`, `PrAndCi`, `ReadyForReview`, and `Blocked` remain the authoritative Windows workflow boundaries.
