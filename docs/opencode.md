# OpenCode frontend

AutoDev's OpenCode integration is an optional cross-platform frontend over shared Python workflow stages. OpenCode owns isolated model conversations; AutoDev remains the source of truth for issue preparation, `.codex-run/current` artifacts, deterministic verification, semantic-verification contracts, repair limits, commits, CI, pull requests, and issue status.

Normal OpenCode execution does **not** require the Windows PowerShell issue-to-PR workflow. Windows and Linux use the same `automation.workflow_stages` backend.

## Portable install / sync

From an AutoDev checkout, the canonical install/update command is:

```text
python -m automation.opencode_adapter install --target-repo <TARGET_REPOSITORY>
```

Use `python3` instead of `python` where that is the installed command.

Examples:

### Windows

```powershell
cd C:\source\AutoDev
python -m automation.opencode_adapter install `
  --target-repo C:\source\repos\TARGET_REPOSITORY

cd C:\source\repos\TARGET_REPOSITORY
opencode
```

The existing convenience wrapper remains available:

```powershell
pwsh -File .\scripts\install-opencode.ps1 `
  -TargetRepository C:\source\repos\TARGET_REPOSITORY
```

### Linux

```bash
cd ~/src/AutoDev
python3 -m automation.opencode_adapter install \
  --target-repo ~/src/TARGET_REPOSITORY \
  --python python3

cd ~/src/TARGET_REPOSITORY
opencode
```

Linux OpenCode execution does not require `pwsh` unless the operator deliberately selects a PowerShell-based local verification command or wrapper.

## Primary workflow

From the target repository, launch OpenCode and run:

```text
/autodev-issue-to-pr 123
```

The command coordinates the normal AutoDev flow from GitHub issue number through a created PR ready for human review. AutoDev never merges the PR automatically.

The command finishes with exactly one top-level state:

```text
PR_READY
BLOCKED
FAILED
```

`PR_READY` means commit/PR/required-CI handling succeeded and the issue was marked ready for human review. `BLOCKED` means an expected deterministic, semantic, or CI gate stopped progress. `FAILED` means setup, subagent execution, or an underlying stage failed unexpectedly.

On `BLOCKED` or `FAILED`, the coordinator reports the issue number, AutoDev branch, completed/failed stage, concise reason, artifact directory, whether the workspace has uncommitted changes relative to the current AutoDev snapshot, whether a commit or PR exists, the PR URL when available, and the recommended next action.

## Shared stage architecture

The frontend shape is:

```text
OpenCode coordinator ───────────────┐
                                    │
Windows/Linux Python CLI ───────────┼─> automation.workflow_stages
                                    │        |
PowerShell/Bash workflows ----------┘        +-> prepare/state/artifacts
                                             +-> local verification/repair
                                             +-> semantic result gate/repair
                                             +-> GitHub API commit
                                             +-> PR reuse/create
                                             +-> required CI/repair
                                             +-> ready/blocked status
```

`automation.workflow_stages` performs no model calls. Model-heavy reader, synthesizer, planner, implementer, fixer, and verifier work stays in isolated OpenCode Tasks (or in the existing provider-backed workflows when OpenCode is not being used).

The coordinator uses the installed portable bridge:

```text
python .opencode/autodev.py stage --name <stage>
```

or:

```text
python3 .opencode/autodev.py stage --name <stage>
```

The bridge reads `.opencode/autodev.json`, adds the configured AutoDev checkout to `PYTHONPATH`, and invokes `automation.opencode_adapter` with the configured Python command.

The stage API returns compact JSON outcomes:

```text
CONTINUE
REPAIR
BLOCKED
FAILED
PR_READY
```

## Coordinator flow

```text
preflight
  -> portable prepare
  -> Task: isolated reader
  -> Task: isolated synthesizer
  -> Task: isolated planner
  -> shared render-implementer stage
  -> Task: isolated implementer
  -> shared local-check stage
       -> repair: isolated fixer -> local-check again
  -> Task: isolated semantic verifier
       -> repair: shared semantic repair artifact -> isolated fixer
                  -> local-check -> semantic verifier again
       -> blocked: stop safely
  -> shared pr-and-ci stage
       -> CI repair: ci-repair.md -> isolated fixer
                     -> local-check -> semantic verifier -> pr-and-ci again
  -> shared ready stage
  -> PR_READY
```

Repair attempts respect:

```text
MAX_REPAIR_ATTEMPTS
MAX_SEMANTIC_REPAIR_ATTEMPTS
```

The defaults remain 3 deterministic/CI repairs and 1 semantic repair unless configured differently.

## Prerequisites

Normal portable OpenCode execution requires:

```text
Python
git
gh
```

`gh` must already be authenticated. AutoDev does not store GitHub or model-provider credentials in the OpenCode assets.

Issue preparation uses the same repository settings expected by the existing workflows:

```text
GITHUB_OWNER
GITHUB_REPO
```

Optional settings include:

```text
BASE_BRANCH
REMOTE_NAME
PROFILES
PROFILES_PATH
LOCAL_CHECK
STACK_CONTEXT
PROMPT_DIR
MAX_REPAIR_ATTEMPTS
MAX_SEMANTIC_REPAIR_ATTEMPTS
```

The resolved `LocalCheck` command is executed using the native platform shell. A local-check configuration may itself require additional stack-specific tools such as `dotnet`, npm, or `pwsh`; that is separate from the OpenCode backend requirement.

If a shared profile file contains a platform-specific verification command, supply a platform-appropriate `PROFILES_PATH` or `LOCAL_CHECK` rather than relying on accidental shell compatibility.

## Installed target-repository files

The idempotent installer creates or refreshes only AutoDev-owned files:

```text
.opencode/
  autodev.json
  autodev.py
  autodev.ps1          # Windows convenience wrapper
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

`autodev.json` contains only the machine-local AutoDev checkout path and Python command. It contains no API keys. Keep it local unless contributors intentionally share identical paths. Command/agent Markdown may be committed if the target repository wants them discoverable for everyone.

## Isolation and durable artifacts

Reader, synthesizer, planner, implementer, fixer, and verifier work runs in isolated OpenCode subagent contexts. Child role agents retain `task: deny`; only the primary coordinator can invoke the six allowlisted AutoDev roles.

State passes through the existing bounded artifacts rather than role chat transcripts:

```text
.codex-run/current/issue.md
.codex-run/current/reader-brief.md
.codex-run/current/synthesized-handoff.md
.codex-run/current/plan.md
.codex-run/current/implementer.md
.codex-run/current/commit-message.txt
.codex-run/current/local-check.log
.codex-run/current/local-repair.md
.codex-run/current/verification-result.json
.codex-run/current/verification/semantic-attempt-*.json
.codex-run/current/verification/final-verdict.json
.codex-run/current/verification-repair.md
.codex-run/current/ci-summary.json
.codex-run/current/ci-repair.md
.codex-run/current/state.json
```

Reader/synthesizer handoffs remain bounded. Planner output continues through AutoDev's existing six-section parser. Semantic JSON continues through the #35 schema and preserves successive `semantic-attempt-N.json` artifacts across repair cycles.

## Coordinator and role permissions

The coordinator has:

```text
edit: deny
read: small current state/verifier-result artifacts only
bash: portable AutoDev bridge plus safe git status/diff
task: deny all, then allow only the six autodev-* role agents
```

Implementer/fixer may edit target source but deny branch/commit/push/PR/issue mutation. Reader/planner/verifier remain read-oriented. Child roles cannot recursively invoke Task.

Both `python .opencode/autodev.py ...` and `python3 .opencode/autodev.py ...` are allowlisted so the same generated agent definitions work with normal Windows and Linux Python command naming.

## Provider and model selection

Checked-in OpenCode commands and agents deliberately do not declare a model. Provider/model selection remains in OpenCode user/project/session configuration; AutoDev does not hardcode Groq, OpenRouter, Ollama, or a specific model.

Example role intent may be:

```text
reader/synthesizer/planner/verifier -> one reasoning model/provider
implementer/fixer                   -> a coding model/provider
coordinator                          -> a smaller orchestration model
```

Explicit role-to-model mapping, effective-mapping inspection, reusable mapping profiles, and safe per-run overrides are tracked separately by #66. Until then, configure the `autodev-*` agents through normal OpenCode configuration.

This OpenCode model selection is separate from AutoDev provider profiles used by the non-OpenCode/headless workflows.

## Ponytail and Headroom

AutoDev's #34 prompt-policy layer is applied when the bridge renders each role prompt. An external OpenCode Ponytail plugin is not required. If one is installed, configure or disable it for `autodev-*` agents so it does not inject a contradictory second policy.

Role isolation and the coordinator do not require Headroom. #36 remains the optional provider-side Headroom implementation for provider-backed workflows; OpenCode does not add another Headroom routing path.

## Advanced/manual role controls

The individual commands remain available for debugging or intentional intervention:

```text
/autodev-read 123
/autodev-plan 123
/autodev-implement 123
/autodev-fix 123
/autodev-verify 123
```

Those commands continue to use `subtask: true`. The synthesizer remains available as the `autodev-synthesizer` subagent.

## Existing workflows remain independent

OpenCode is optional. Existing entrypoints remain usable without OpenCode:

```text
scripts/run-real-issue.ps1
windows/scripts/issue-to-pr-cycle.ps1
linux/scripts/issue-to-pr-cycle.sh
automation.prompt_runner
automation.run_real_issue
```

OpenCode no longer uses `windows/scripts/issue-to-pr-cycle.ps1` as its backend. PowerShell and Bash remain supported frontends, while portable OpenCode stages use the shared Python implementation.
