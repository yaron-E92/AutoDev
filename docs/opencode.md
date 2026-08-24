# OpenCode frontend

AutoDev's OpenCode integration is an optional cross-platform frontend over shared Python workflow stages. OpenCode owns isolated model conversations; AutoDev remains the source of truth for issue preparation, `.autodev-run/current` artifacts, deterministic verification, semantic-verification contracts, repair limits, commits, CI, pull requests, and issue status.

Normal OpenCode execution does **not** require the Windows PowerShell issue-to-PR workflow. Windows and Linux use the same `automation.workflow_stages` backend.

## Portable install / sync

From an AutoDev checkout, the canonical complete install/update command is:

```text
python -m automation.opencode_install --target-repo <TARGET_REPOSITORY>
```

Use `python3` instead of `python` where that is the installed command. The older `python -m automation.opencode_adapter install` command is deprecated; it remains only as a compatibility shim and delegates to `automation.opencode_install` so it cannot silently install a smaller asset set.

Examples:

### Windows

```powershell
cd C:\source\AutoDev
python -m automation.opencode_install `
  --target-repo C:\source\repos\TARGET_REPOSITORY

cd C:\source\repos\TARGET_REPOSITORY
opencode
```

### Linux

```bash
cd ~/src/AutoDev
python3 -m automation.opencode_install \
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

### Durable status and resume

OpenCode chat history is not workflow memory. Each new OpenCode issue-to-PR run also checkpoints #37's durable manifest at:

```text
.autodev-run/current/run-manifest.json
```

After an interruption or in a fresh OpenCode process, use:

```text
/autodev-status
/autodev-resume
```

`/autodev-status` is read-only and reports completed stages, the next valid action, repair counters, drift/resume blockers, commit/PR identity, and the next #66 model role/model. `/autodev-resume` validates the manifest/artifacts, prepared repository/base identity, direct-edit source identity, and any #69 shipped-tree/PR-head/CI proof before entering the existing coordinator at the manifest-selected boundary. Completed model-heavy work is not replayed merely because the OpenCode process changed.

Role-model changes continue to use normal #66 OpenCode configuration. Use `--invalidate-role <role>` only when intentionally invalidating completed #37 checkpoints affected by a changed role configuration.

See [OpenCode status and resume](opencode-resume.md) for interruption/recovery details and the Windows-first resume flow.

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
.autodev-run/current/role-contracts.json
```

The generated contract covers reader, synthesizer, planner, implementer, fixer, and verifier, including the exact prepare/accept commands, required output artifact, format constraints, and bounded handoff size where applicable.

Planner preparation also writes:

```text
.autodev-run/current/plan.template.md
```

from the same six headings used by the existing planner parser.

Verifier preparation writes:

```text
.autodev-run/current/verification-result.template.json
```

from the canonical semantic-verifier schema. Detectable acceptance criteria are pre-populated verbatim. The verifier fills parser-supported values only; a clean pass may use an empty `findings` array.

When a reader/planner/implementer/verifier protocol artifact is malformed, AutoDev allows **one** format-correction attempt for that role invocation. It writes:

```text
.autodev-run/current/contract-correction-<role>.md
```

with the complete validation error, exact role contract, generated template where applicable, a bounded copy of the rejected artifact, and the exact accept command to rerun. A second rejection is terminal. This correction allowance is separate from deterministic code repair, semantic code repair, and CI repair limits.

Accepted role artifacts are SHA-256 pinned in `state.json`. OpenCode stages that depend on a model-produced artifact fail before further work if that accepted artifact is missing or changed.

The coordinator-specific implementer path is intentionally different from the standalone `/autodev-implement` command: after `stage --name render-implementer`, the implementer reads the already-rendered `.autodev-run/current/implementer.md` and **does not prepare/render it again**.

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
.autodev-run/current/run-diagnostics.json
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

The idempotent canonical installer creates or refreshes only AutoDev-owned files:

```text
.github/
  workflows/
    autodev-windows-verification.yml
.opencode/
  autodev.json
  autodev.py
  autodev.ps1          # Windows convenience wrapper
  commands/
    autodev-issue-to-pr.md
    autodev-status.md
    autodev-resume.md
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

The Windows workflow is stable across ordinary AutoDev upgrades: it receives the exact AutoDev commit as the per-run `autodev_ref` dispatch input rather than hardcoding the installer-time SHA. Re-running the installer therefore should not create a workflow diff merely because AutoDev moved to a newer commit. Commit/merge the workflow to the target default branch when it is first installed or when its protocol/template actually changes.

Running the installer again updates those named files and leaves unrelated `.opencode` commands, agents, and configuration untouched. It does not create, edit, or replace project `opencode.json` / `opencode.jsonc` files, so project role-model mappings remain user-owned.

`autodev.json` contains only the machine-local AutoDev checkout path and Python command. It contains no API keys. Keep it local unless contributors intentionally share identical paths. Command/agent Markdown may be committed if the target repository wants them discoverable for everyone.

## Isolation and durable artifacts

Reader, synthesizer, planner, implementer, fixer, and verifier work runs in isolated OpenCode subagent contexts. Child role agents retain `task: deny`; only the primary coordinator can invoke the six allowlisted AutoDev roles.

State passes through bounded artifacts rather than role chat transcripts:

```text
.autodev-run/current/issue.md
.autodev-run/current/run-manifest.json
.autodev-run/current/role-contracts.json
.autodev-run/current/reader-brief.md
.autodev-run/current/synthesized-handoff.md
.autodev-run/current/plan.template.md
.autodev-run/current/plan.md
.autodev-run/current/implementer.md
.autodev-run/current/commit-message.txt
.autodev-run/current/local-check.log
.autodev-run/current/local-repair.md
.autodev-run/current/verification-result.template.json
.autodev-run/current/verification-result.json
.autodev-run/current/verification/semantic-attempt-*.json
.autodev-run/current/verification/final-verdict.json
.autodev-run/current/verification-repair.md
.autodev-run/current/ci-summary.json
.autodev-run/current/ci-repair.md
.autodev-run/current/contract-correction-<role>.md
.autodev-run/current/run-diagnostics.json
.autodev-run/current/state.json
```

`run-manifest.json` is the #37 authority for completed stages, invalidation, failure, and next-stage/resume decisions. Reader/synthesizer handoffs remain bounded. Planner output continues through AutoDev's existing six-section parser. Semantic JSON continues through the #35 schema and preserves successive `semantic-attempt-N.json` artifacts across repair cycles.

## Coordinator and role permissions

The coordinator has:

```text
edit: deny
read: small current state/manifest/diagnostic/contract/verifier-result artifacts only
bash: exact AutoDev stage/status/resume bridge commands plus safe git status/diff
task: deny all, then allow only the six autodev-* role agents
```

Implementer/fixer may edit target source but still deny branch/commit/push/PR/issue mutation. Routine `git status`, `git diff`, `dotnet restore`, `dotnet build`, `dotnet test`, and directory creation are explicitly allowlisted where needed so normal implementation/verification does not degrade into repeated approval prompts. Reader/planner remain read-oriented, and the verifier may write only the designated semantic result. Child roles cannot recursively invoke Task.

Role agents allow only their legal AutoDev `prepare`/`accept` bridge forms instead of a wildcard bridge permission. Both `python` and `python3` forms are present for normal Windows/Linux command naming.

## Role → model mapping

OpenCode, not AutoDev, is the model-routing source of truth for the OpenCode frontend. OpenCode model IDs use `provider/model-id` syntax, and OpenCode configuration can assign a `model` to each custom agent. The checked-in AutoDev agents intentionally contain no `model:` field so installing or syncing AutoDev never hardcodes a provider or model.

OpenCode's normal resolution rules apply:

- an explicit `agent.<name>.model` wins for that agent;
- an unmapped primary agent uses OpenCode's globally configured/current model;
- an unmapped subagent inherits the model of the primary agent that invoked it;
- for `/autodev-issue-to-pr`, that invoking primary is `autodev-coordinator`;
- for standalone `/autodev-read`, `/autodev-plan`, `/autodev-implement`, `/autodev-fix`, and `/autodev-verify`, the subagent inherits from the primary agent invoking that standalone command unless the AutoDev role has its own explicit mapping.

| AutoDev role | OpenCode agent | Explicit model | If omitted |
| --- | --- | --- | --- |
| Coordinator | `autodev-coordinator` | configurable | OpenCode global/current primary model |
| Reader | `autodev-reader` | configurable | invoking primary; coordinator in issue-to-PR |
| Synthesizer | `autodev-synthesizer` | configurable | invoking primary; coordinator in issue-to-PR |
| Planner | `autodev-planner` | configurable | invoking primary; coordinator in issue-to-PR |
| Implementer | `autodev-implementer` | configurable | invoking primary; coordinator in issue-to-PR |
| Fixer | `autodev-fixer` | configurable | invoking primary; coordinator in issue-to-PR |
| Verifier | `autodev-verifier` | configurable | invoking primary; coordinator in issue-to-PR |

Configure these mappings in ordinary OpenCode project/user configuration. For a target repository, use its existing `opencode.json` or `opencode.jsonc`; the AutoDev installer does not modify either file.

### Inspect the resolved mapping

Before running an issue-to-PR cycle:

```text
python -m automation.opencode_adapter models --repo .
```

or, in a target repo with the portable bridge installed:

```text
python .opencode/autodev.py models --repo .
```

Use `python3` where appropriate. The command asks OpenCode for its merged resolved configuration with `opencode debug config`, extracts only the global model plus the seven `autodev-*` agent mappings, validates them, and prints only model-resolution information. It never prints or persists the rest of the resolved OpenCode configuration.

Example output:

```text
AutoDev OpenCode role models:
coordinator   groq/<coordinator-model> (explicit)
reader        groq/<coordinator-model> (inherited from autodev-coordinator during /autodev-issue-to-pr; invoking primary for standalone role commands)
synthesizer   groq/<coordinator-model> (inherited from autodev-coordinator during /autodev-issue-to-pr; invoking primary for standalone role commands)
planner       groq/<planner-model> (explicit)
implementer   openrouter/<implementation-model> (explicit)
fixer         openrouter/<implementation-model> (explicit)
verifier      groq/<verifier-model> (explicit)
```

If neither an AutoDev role nor the static global config resolves to a concrete model, introspection reports inheritance from the OpenCode current/default model instead of guessing. A model selected dynamically inside an already-running TUI with `/models` is session state and may therefore be more specific than a separate `opencode debug config` process can prove.

The coordinator preflight performs the same structural validation before reader/planner/etc. work. AutoDev rejects malformed explicit `provider/model` identifiers and `agent.autodev-*` model mappings for unknown AutoDev roles. It does not guess provider availability, authentication, billing, or free-tier eligibility; those remain OpenCode/provider responsibilities.

### Example 1: one model for every role

A global OpenCode model is enough when all AutoDev roles should follow the same model:

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "model": "provider/<model-id>"
}
```

`autodev-coordinator` uses that primary/default model, and its unmapped child roles inherit it during `/autodev-issue-to-pr`.

### Example 2: mixed Groq/OpenRouter roles

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "agent": {
    "autodev-coordinator": {
      "model": "groq/<coordinator-model>"
    },
    "autodev-reader": {
      "model": "groq/<reader-model>"
    },
    "autodev-synthesizer": {
      "model": "groq/<synthesizer-model>"
    },
    "autodev-planner": {
      "model": "groq/<planner-model>"
    },
    "autodev-implementer": {
      "model": "openrouter/<implementation-model>"
    },
    "autodev-fixer": {
      "model": "openrouter/<fixer-model>"
    },
    "autodev-verifier": {
      "model": "groq/<verifier-model>"
    }
  }
}
```

The provider/model values above are placeholders, not promises about availability, price, or a free tier.

### Example 3: all-local Ollama

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "model": "ollama/<local-model>"
}
```

All unmapped AutoDev roles follow the local OpenCode model. You may still override individual `autodev-*` agents if different local models are useful.

### Example 4: cheap coordinator + stronger implementer

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "agent": {
    "autodev-coordinator": {
      "model": "provider/<lightweight-model>"
    },
    "autodev-implementer": {
      "model": "provider/<strong-coding-model>"
    },
    "autodev-fixer": {
      "model": "provider/<strong-coding-model>"
    }
  }
}
```

During `/autodev-issue-to-pr`, reader/synthesizer/planner/verifier inherit the coordinator model in this example, while implementer/fixer use the stronger explicit mapping.

### `/models`, credentials, and free routes

OpenCode's `/models` command changes the active/default model for the session. It does not replace an explicit `agent.autodev-*.model` mapping. Use explicit agent mappings when a role must stay pinned to a particular route.

Credentials remain in normal OpenCode/provider/user environment configuration. Do not put API keys in `.opencode/autodev.json`, AutoDev agent Markdown, or role-model examples.

A model that is free today may change availability or pricing later. AutoDev therefore does not label any provider/model as guaranteed free and never chooses a paid/default fallback when an explicit AutoDev role mapping is malformed. Mapping errors fail visibly.

### Per-run model flags are intentionally unsupported

Current documented OpenCode behavior provides agent-level and command-level model configuration, but does not provide AutoDev with a documented per-Task child-model selector that the coordinator can safely apply independently to each child invocation. Therefore these forms are rejected rather than ignored:

```text
/autodev-issue-to-pr 123 --model planner=provider/model-a
/autodev-issue-to-pr 123 --role-model-profile free-cloud
```

Configure `agent.autodev-*.model` in OpenCode configuration before starting the session instead. AutoDev does not rewrite agent Markdown, duplicate model-specific agents, restart OpenCode, or mutate global user configuration behind the user's back.

Named AutoDev role-model profiles are not added here because OpenCode already provides reusable config files through its normal configuration mechanisms, including custom config selected before startup with `OPENCODE_CONFIG`. This avoids creating a second model-routing layer that competes with OpenCode.

### OpenCode mapping vs headless AutoDev provider profiles

These are separate configuration surfaces:

```text
OpenCode role-model mapping
  -> opencode.json / opencode.jsonc
  -> agent.autodev-*.model
  -> controls OpenCode agents

AutoDev provider profile
  -> provider-profile JSON used by the platform workflows / automation.prompt_runner
  -> controls non-OpenCode/headless model transports
```

Changing one does not configure the other.

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
```

OpenCode does not use `windows/scripts/issue-to-pr-cycle.ps1` as its backend. PowerShell and Bash remain supported frontends, while portable OpenCode stages use the shared Python implementation.
