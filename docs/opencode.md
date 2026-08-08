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

`PR_READY` means commit/PR/required-CI handling succeeded and the issue was marked ready for human review. `BLOCKED` means an expected bounded repair loop or semantic decision stopped progress. `FAILED` means a stage cannot safely continue, including deterministic setup/protocol failures and exhausted unchanged retries.

Stage JSON also reports a failure classification when applicable:

```text
code-repairable
transient/retryable-infrastructure
non-retryable-deterministic
```

Only `code-repairable` outcomes are delegated to the fixer. A deterministic failure is not retried unchanged merely because another model turn is available.

On `BLOCKED` or `FAILED`, the coordinator reports the issue number, AutoDev branch, completed/failed stage, failure classification, concise reason, artifact directory, whether the workspace has uncommitted changes relative to the current AutoDev snapshot, whether a commit or PR exists, the PR URL when available, and the recommended next action.

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

The coordinator uses the installed portable bridge with exact stage invocations such as:

```text
python .opencode/autodev.py stage --name preflight --arguments "123"
python .opencode/autodev.py stage --name prepare --arguments "123"
python .opencode/autodev.py stage --name render-implementer
python .opencode/autodev.py stage --name local-check --attempt 0
python .opencode/autodev.py stage --name semantic --attempt 0
python .opencode/autodev.py stage --name pr-and-ci --attempt 0
python .opencode/autodev.py stage --name ready
```

Use `python3` instead only where that is the available Python command. Models are not expected to invent bridge verbs or abbreviated subcommands.

The bridge reads `.opencode/autodev.json`, adds the configured AutoDev checkout to `PYTHONPATH`, and invokes `automation.opencode_adapter` with the configured Python command.

The stage API returns compact JSON outcomes:

```text
CONTINUE
REPAIR
BLOCKED
FAILED
PR_READY
```

## Deterministic role protocol

Python, not the model conversation, defines each role's legal bridge commands and output contract. Preparing an OpenCode run writes:

```text
.codex-run/current/role-contracts.json
```

The generated contract covers reader, synthesizer, planner, implementer, fixer, and verifier, including the exact prepare/accept commands, required output artifact, format constraints, and bounded handoff size where applicable.

Planner preparation also writes:

```text
.codex-run/current/plan.template.md
```

from the same six headings used by the existing planner parser.

Verifier preparation writes:

```text
.codex-run/current/verification-result.template.json
```

from the canonical semantic-verifier schema. Detectable acceptance criteria are pre-populated verbatim. The verifier fills parser-supported values only; a clean pass may use an empty `findings` array.

When a reader/planner/implementer/verifier protocol artifact is malformed, AutoDev allows **one** format-correction attempt for that role invocation. It writes:

```text
.codex-run/current/contract-correction-<role>.md
```

with the complete validation error, exact role contract, generated template where applicable, a bounded copy of the rejected artifact, and the exact accept command to rerun. A second rejection is terminal. This correction allowance is separate from deterministic code repair, semantic code repair, and CI repair limits.

Accepted role artifacts are SHA-256 pinned in `state.json`. OpenCode stages that depend on a model-produced artifact fail before further work if that accepted artifact is missing or changed.

The coordinator-specific implementer path is intentionally different from the standalone `/autodev-implement` command: after `stage --name render-implementer`, the implementer reads the already-rendered `.codex-run/current/implementer.md` and **does not prepare/render it again**.

## Coordinator flow

```text
preflight
  -> portable prepare
  -> Task: isolated reader -> exact accept
  -> Task: isolated synthesizer -> exact accept
  -> Task: isolated planner -> exact accept
  -> shared render-implementer stage
  -> Task: isolated implementer -> exact accept
  -> shared local-check stage
       -> code-repairable: isolated fixer -> local-check again
  -> Task: isolated semantic verifier -> exact accept
  -> shared semantic stage
       -> code-repairable: shared semantic repair artifact -> isolated fixer
                           -> local-check -> semantic verifier again
       -> blocked/deterministic failure: stop safely
  -> shared pr-and-ci stage
       -> code-repairable CI: ci-repair.md -> isolated fixer
                              -> local-check -> semantic verifier -> pr-and-ci again
       -> base/ref/tree/setup failure: fail without fixer
  -> shared ready stage
  -> PR_READY
```

Repair attempts respect:

```text
MAX_REPAIR_ATTEMPTS
MAX_SEMANTIC_REPAIR_ATTEMPTS
```

The defaults remain 3 deterministic/CI repairs and 1 semantic repair unless configured differently.

AutoDev fingerprints deterministic stage failures from bounded state/artifact/workspace hashes. Repeating the same deterministic stage with unchanged relevant inputs returns the previous failure as `repeated_failure: true` instead of executing it again.

## Subprocess and GitHub diagnostics

Captured subprocess output is decoded explicitly as UTF-8 with replacement on both Windows and Linux. Invalid console bytes therefore cannot crash the workflow with the host locale decoder. Replacement-decoded **machine JSON is still parsed strictly**: corrupted `gh` JSON fails deterministically rather than being accepted.

GitHub failures retain the original process exit code plus bounded stderr/stdout evidence. Prepare validates the remote base commit and `tree.sha` before persisting the prepared run. API commit creation reuses that prepared `BaseTreeSha`; older/missing state is resolved from the exact prepared parent commit, never silently from local `HEAD`.

Lightweight counters and timings are persisted in:

```text
.codex-run/current/run-diagnostics.json
```

including role invocations, protocol-correction attempts, stage invocations, repeated identical deterministic failures, and per-stage wall time. Secrets and model transcripts are not fingerprint inputs.

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

State passes through bounded artifacts rather than role chat transcripts:

```text
.codex-run/current/issue.md
.codex-run/current/role-contracts.json
.codex-run/current/reader-brief.md
.codex-run/current/synthesized-handoff.md
.codex-run/current/plan.template.md
.codex-run/current/plan.md
.codex-run/current/implementer.md
.codex-run/current/commit-message.txt
.codex-run/current/local-check.log
.codex-run/current/local-repair.md
.codex-run/current/verification-result.template.json
.codex-run/current/verification-result.json
.codex-run/current/verification/semantic-attempt-*.json
.codex-run/current/verification/final-verdict.json
.codex-run/current/verification-repair.md
.codex-run/current/ci-summary.json
.codex-run/current/ci-repair.md
.codex-run/current/contract-correction-<role>.md
.codex-run/current/run-diagnostics.json
.codex-run/current/state.json
```

Reader/synthesizer handoffs remain bounded. Planner output continues through AutoDev's existing six-section parser. Semantic JSON continues through the #35 schema and preserves successive `semantic-attempt-N.json` artifacts across repair cycles.

## Coordinator and role permissions

The coordinator has:

```text
edit: deny
read: small current state/diagnostic/contract/verifier-result artifacts only
bash: exact AutoDev stage bridge commands plus safe git status/diff
task: deny all, then allow only the six autodev-* role agents
```

Implementer/fixer may edit target source but still deny branch/commit/push/PR/issue mutation. Routine `git status`, `git diff`, `dotnet restore`, `dotnet build`, `dotnet test`, and directory creation are explicitly allowlisted where needed so normal implementation/verification does not degrade into repeated approval prompts. Reader/planner remain read-oriented, and the verifier may write only the designated semantic result. Child roles cannot recursively invoke Task.

Role agents allow only their legal AutoDev `prepare`/`accept` bridge forms instead of a wildcard bridge permission. Both `python` and `python3` forms are present for normal Windows/Linux command naming.

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

Role isolation and the coordinator do not require Headroom. #36 remains optional. After deterministic CI is clean, an operator who intentionally uses the Headroom CLI with OpenCode can run the real smoke path from a target repository as:

```text
headroom wrap opencode
```

then invoke `/autodev-issue-to-pr <issue>`. That real provider/tool smoke run is intentionally operator-run and is not part of ordinary offline/mock CI.

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

OpenCode does not use `windows/scripts/issue-to-pr-cycle.ps1` as its backend. PowerShell and Bash remain supported frontends, while portable OpenCode stages use the shared Python implementation.
