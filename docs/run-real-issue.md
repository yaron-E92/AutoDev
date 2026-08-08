# Running the provider-agnostic issue-to-PR workflow

This is the canonical end-to-end guide for running AutoDev with Groq, OpenRouter, local Ollama, Codex CLI, or another supported provider.

The normal workflow remains:

```text
GitHub issue
  -> prepare and area-read the repository
  -> planner
  -> implementer
  -> deterministic local checks
  -> fixer when needed
  -> pull request and CI
  -> verifier
  -> ready-for-review status
```

Provider selection is configured in one JSON profile. PowerShell and Bash only orchestrate stages; Python resolves roles, constructs requests, invokes providers, records telemetry, and parses model output.

Codex Desktop is optional. A complete run can be headless.

## 1. Choose a checked-in provider profile

AutoDev includes three examples:

```text
examples/providers/groq-openrouter-free.json
examples/providers/ollama-local-all-roles.json
examples/providers/codex-command-profile.json
```

### Mixed Groq and OpenRouter

`examples/providers/groq-openrouter-free.json` maps:

```text
reader       -> Groq
synthesizer  -> Groq
planner      -> Groq
implementer  -> OpenRouter :free model
fixer        -> OpenRouter :free model
verifier     -> Groq
```

Copy the example before editing it:

```powershell
Copy-Item examples\providers\groq-openrouter-free.json "$env:USERPROFILE\autodev-groq-openrouter.json"
```

```bash
cp examples/providers/groq-openrouter-free.json ~/autodev-groq-openrouter.json
```

Replace:

```text
REPLACE_WITH_OPENROUTER_MODEL:free
```

with a currently available OpenRouter model whose identifier ends in `:free`.

Do not remove:

```json
"free_only": true
```

With `free_only` enabled, AutoDev rejects paid models and paid fallbacks and sends provider routing options that disable silent fallback. If the selected free model is unavailable, the invocation fails instead of using a paid route.

### Local Ollama

`examples/providers/ollama-local-all-roles.json` runs all six roles through the generic command transport.

Install Ollama, pull the configured models, and ensure the local service is available. The profile can then be used without API keys.

### Codex CLI command profile

`examples/providers/codex-command-profile.json` demonstrates Codex CLI as a generic command provider.

The implementer and fixer entries use:

```json
"direct_edit": true
```

That means the command edits the workspace directly. HTTP providers and command providers without `direct_edit` must return AutoDev's exact patch contract instead.

## 2. Configure credentials

Profiles contain only environment-variable names. Never put API-key values in the JSON file.

For the mixed profile, set:

### Windows PowerShell

```powershell
$env:GROQ_API_KEY = "your-groq-key"
$env:OPENROUTER_API_KEY = "your-openrouter-key"
```

### Linux

```bash
export GROQ_API_KEY="your-groq-key"
export OPENROUTER_API_KEY="your-openrouter-key"
```

The resulting telemetry records only the variable names and whether they were configured. It does not persist the values.

## 3. Run provider preflight first

Preflight does not select an issue, change labels, create a branch, modify the target repository, or contact GitHub.

It checks:

- profile structure;
- required environment variables;
- command executables;
- endpoint reachability;
- model accessibility when the provider exposes a safe model endpoint;
- free-only configuration validity.

### Direct Python preflight

Windows:

```powershell
python -m automation.provider_preflight `
  --provider-profile "$env:USERPROFILE\autodev-groq-openrouter.json" `
  --out .autodev-run\provider-preflight.json
```

Linux:

```bash
python3 -m automation.provider_preflight \
  --provider-profile ~/autodev-groq-openrouter.json \
  --out .autodev-run/provider-preflight.json
```

### Through the existing Windows entrypoint

From the AutoDev checkout:

```powershell
scripts\run-real-issue.ps1 `
  -Mode Preflight `
  -ProviderProfile "$env:USERPROFILE\autodev-groq-openrouter.json"
```

### Through the existing Linux entrypoint

The Linux wrapper still loads the project environment file:

```bash
scripts/run-real-issue.sh \
  --env ~/automation/state/PROJECT.env \
  --mode Preflight \
  --provider-profile ~/autodev-groq-openrouter.json
```

A successful report ends with:

```json
"status": "success"
```

Do not start a full run until preflight succeeds.

## 4. Run one complete issue-to-PR cycle

The target repository must already use the trusted AutoDev scripts and labels. The workflow never merges the pull request.

### Windows PowerShell

Run from the AutoDev checkout and point `-WorkingDirectory` at the target repository:

```powershell
scripts\run-real-issue.ps1 `
  -Mode Run `
  -WorkingDirectory "C:\repos\TARGET_REPOSITORY" `
  -Username "OWNER" `
  -Repo "REPOSITORY" `
  -Base "main" `
  -Remote "origin" `
  -ProviderProfile "$env:USERPROFILE\autodev-groq-openrouter.json"
```

To process a specific issue:

```powershell
scripts\run-real-issue.ps1 `
  -Mode Run `
  -WorkingDirectory "C:\repos\TARGET_REPOSITORY" `
  -Username "OWNER" `
  -Repo "REPOSITORY" `
  -Issue 46 `
  -ProviderProfile "$env:USERPROFILE\autodev-groq-openrouter.json"
```

Without `-Issue`, AutoDev selects the next open issue labeled `autodev:ready` that is not already running or blocked.

For a local task without a GitHub issue:

```powershell
scripts\run-real-issue.ps1 `
  -Mode Run `
  -WorkingDirectory "C:\repos\TARGET_REPOSITORY" `
  -Username "OWNER" `
  -Repo "REPOSITORY" `
  -DescriptionFile "C:\temp\task.md" `
  -ProviderProfile "$env:USERPROFILE\autodev-groq-openrouter.json"
```

### Linux

Run from the target repository, using the AutoDev wrapper path:

```bash
~/repos/AutoDev/scripts/run-real-issue.sh \
  --env ~/automation/state/PROJECT.env \
  --mode Run \
  --owner OWNER \
  --repo REPOSITORY \
  --base main \
  --remote origin \
  --provider-profile ~/autodev-groq-openrouter.json
```

For a specific issue:

```bash
~/repos/AutoDev/scripts/run-real-issue.sh \
  --env ~/automation/state/PROJECT.env \
  --mode Run \
  --owner OWNER \
  --repo REPOSITORY \
  --issue 46 \
  --provider-profile ~/autodev-groq-openrouter.json
```

For literal task text:

```bash
~/repos/AutoDev/scripts/run-real-issue.sh \
  --env ~/automation/state/PROJECT.env \
  --mode Run \
  --owner OWNER \
  --repo REPOSITORY \
  --description-file /tmp/task.md \
  --provider-profile ~/autodev-groq-openrouter.json
```

## 5. Run only one workflow stage

The existing transition modes remain available:

```text
Preflight
Prepare
Plan
RenderImplementerPrompt
LocalCheck
PrAndCi
RenderVerificationRepair
ReadyForReview
Blocked
```

Examples:

```powershell
scripts\run-real-issue.ps1 `
  -Mode Plan `
  -WorkingDirectory "C:\repos\TARGET_REPOSITORY" `
  -Username OWNER `
  -Repo REPOSITORY `
  -ProviderProfile "$env:USERPROFILE\autodev-groq-openrouter.json"
```

```bash
~/repos/AutoDev/scripts/run-real-issue.sh \
  --env ~/automation/state/PROJECT.env \
  --mode Plan \
  --owner OWNER \
  --repo REPOSITORY \
  --provider-profile ~/autodev-groq-openrouter.json
```

## 6. Understand the two command-provider modes

### Raw text or patch mode

HTTP transports and ordinary command transports return text to AutoDev.

Implementer output must be either:

```text
NO_CHANGES_REQUIRED
<optional explanation>
```

or:

```text
COMMIT_MESSAGE: concise imperative commit message
BEGIN_UNIFIED_DIFF
<unified diff applicable with git apply>
END_UNIFIED_DIFF
```

Fixer output uses the same format without requiring `COMMIT_MESSAGE`.

Verifier output must start with exactly:

```text
PASS
```

or:

```text
FAIL
```

Provider metadata, token usage, cost, HTTP diagnostics, and reasoning summaries are recorded separately and never enter these parsers.

### Direct-edit command mode

A command profile may set:

```json
"direct_edit": true
```

for implementer or fixer roles. The command then edits the workspace itself rather than returning a unified diff.

A direct-edit implementer must also write the requested commit-message file, or return a `COMMIT_MESSAGE:` line that AutoDev can extract.

Do not use `direct_edit` for HTTP transports.

## 7. Provider-profile schema

A version-2 profile uses independent role entries:

```json
{
  "version": 2,
  "name": "my-provider-profile",
  "roles": {
    "reader": {
      "transport": "openai-compatible-chat-completions",
      "model": "provider/model",
      "base_url": "https://provider.example/v1",
      "api_key_env": "PROVIDER_API_KEY",
      "timeout_seconds": 600,
      "headers": {
        "HTTP-Referer": "https://example.invalid/autodev",
        "X-Title": "AutoDev"
      },
      "request_options": {}
    }
  }
}
```

Supported transports:

```text
command
openai-compatible-chat-completions
openai-compatible-responses
mock
```

Backward-compatible aliases include:

```text
chat-completions
openai-compatible
responses
ollama
```

Optional role fields:

```text
transport or provider
model
base_url
api_key_env
timeout_seconds
headers
request_options
output_limit
command
profile_name
free_only
fallback_models
direct_edit
```

`headers` are allowlisted. Authorization, cookies, API-key headers, and other secret-bearing headers are rejected.

Chat Completions omits `max_tokens` unless `output_limit` is explicitly configured. Responses omits `max_output_tokens` unless explicitly configured.

## 8. Legacy command-line compatibility

The existing planner and agent flags remain accepted:

Windows:

```text
-PlannerProvider
-PlannerModel
-PlannerAgentCommand
-AgentProvider
-AgentModel
-AgentCommand
```

Linux:

```text
--planner-provider
--planner-model
--planner-agent-command
--agent-provider
--agent-model
--agent-command
```

Supplying only a model retains the historical Ollama behavior and derives:

```text
ollama run <model>
```

The shells no longer validate provider names. Python performs provider validation and error classification.

Use a provider profile for new configurations. Legacy flags are primarily for backward compatibility and temporary overrides.

## 9. Outputs and telemetry

The trusted script workflow writes its current state beneath:

```text
.autodev-run/current/
```

Important files include:

```text
issue.md
planner.md
plan.md
implementer.md
commit-message.txt
verification-result.md
model-invocations.json
provider-preflight.json
state.json
```

`model-invocations.json` records safe per-call information where available:

```text
role
transport
profile name
configured model
reported model
start and end timestamps
elapsed time
success or failure
retry count
token usage
reported cost
sanitized failure classification
```

AutoDev does not invent usage or cost when the provider does not report them.

## 10. Troubleshooting

### Authentication failure

```text
authentication_failed
HTTP 401
```

Check the environment-variable name in the profile and confirm the variable is available to the process running AutoDev.

### Payment or plan required

```text
payment_required
HTTP 402
```

The provider rejected the account or route for plan or billing reasons. AutoDev does not switch to a paid fallback automatically.

### Endpoint or model missing

```text
not_found
HTTP 404
```

Check `base_url`, the selected transport endpoint, and the exact model identifier.

### Rate limit or quota exhausted

```text
rate_limited
HTTP 429
```

Retry later or explicitly configure another eligible fallback. For `free_only` profiles, every configured fallback must also end in `:free`.

### Malformed response

```text
malformed_response
```

The provider returned invalid JSON or omitted the expected assistant/output text field.

### Timeout or unreachable endpoint

```text
timeout
transport_error
```

Check connectivity and increase `timeout_seconds` only when the provider is otherwise healthy.

### Command unavailable

```text
command_unavailable
```

Install the configured executable or correct the command template. Preflight checks the first executable in the command.

## 11. Python patch-contract runner

`automation.run_real_issue` owns the Python planning, implementation, deterministic verification, semantic verification, resumable checkpoint, and draft-PR flow:

```bash
python3 -m automation.run_real_issue \
  --repo /path/to/repository \
  --github-repo OWNER/REPOSITORY \
  --issue 46 \
  --mode plan-only \
  --provider-config ~/autodev-groq-openrouter.json \
  --out /tmp/autodev-run
```

For the normal trusted Windows or Linux shell workflow, use `scripts/run-real-issue.ps1` or `scripts/run-real-issue.sh` as documented above. The resumable manifest described below belongs to the Python patch-contract runner.

## 12. Resume an interrupted Python run

Every Python patch-contract run writes a versioned manifest to:

```text
<out-directory>/run-manifest.json
```

The manifest records the target repository and issue, original base SHA, AutoDev branch, completed stages, artifact hashes, execution-affecting role/config fingerprints, safe provider metadata, provider attempts, usage/cost when reported, failure classification, semantic-verification state, Headroom compression metadata when available, and PR identity when one has been created.

Secret values are not stored or hashed. API-key environment-variable names may be recorded; their resolved values are not.

The checkpoint order is:

```text
issue-selected
repository-read
handoff-synthesized
plan-created
implementation-generated
patch-applied
deterministic-verified
semantic-verified
pr-created
```

`repair-generated` is recorded when a deterministic or semantic fixer produces an intermediate repair patch.

### Inspect status without resuming

```bash
python3 -m automation.run_real_issue \
  --resume /tmp/autodev-run \
  --status
```

Status reports the completed stages, next stage, resolved role/provider/model metadata, recorded provider attempts/failures, requested role-invalidation preview, and artifact drift discovered from the manifest hashes.

### Resume with the original configuration

Restore any required credentials or local provider availability, return the target repository to the recorded AutoDev branch/worktree, then run:

```bash
python3 -m automation.run_real_issue \
  --resume /tmp/autodev-run
```

The runner restores the repository, GitHub issue, mode, provider-profile path, fix-attempt limit, baseline-verification option, debug-artifact option, and managed-label behavior from the manifest. You do not need to repeat `--repo`, `--github-repo`, `--issue`, `--mode`, or `--out`.

A provider/quota failure is resumable after the provider becomes available again. Completed reader/planner/implementation stages are not repeated merely because a later provider returned `429`, `402`, timeout, or another recorded provider failure.

The next model-backed stage validates its resolved role configuration and required API-key environment variable before invoking the provider. Actual provider/model/fallback behavior remains the provider-neutral behavior documented earlier in this guide.

### Resume after changing a future role/model

You may edit or replace the provider profile for a role that has not yet contributed to a completed stage. For example, after planning but before implementation, changing only the implementer model does not require recomputing reader, synthesis, or planner work:

```bash
python3 -m automation.run_real_issue \
  --resume /tmp/autodev-run \
  --provider-config ~/autodev-new-implementer.json
```

If the changed role already produced completed work, AutoDev refuses to continue silently. Explicitly invalidate that role:

```bash
python3 -m automation.run_real_issue \
  --resume /tmp/autodev-run \
  --provider-config ~/autodev-new-planner.json \
  --invalidate-role planner
```

Invalidation is deterministic:

```text
reader       -> repository-read and downstream
synthesizer  -> handoff-synthesized and downstream
planner      -> plan-created and downstream
implementer  -> implementation-generated and downstream
fixer        -> repair-generated and dependent verification
verifier     -> semantic-verified and downstream PR state
```

Changing a planner therefore reuses completed reader and synthesis artifacts. Changing a synthesizer reuses completed reader artifacts. Changing an implementer after planning reuses the entire planning pipeline.

`free_only`, explicit fallback models, request options, model IDs, transport, Headroom settings, output limits, command configuration, and prompt-policy metadata are part of the execution fingerprint. They cannot silently change completed work. Secret-value rotation in the same configured environment variable does not invalidate a stage.

### Drift rules

Resume fails closed when completed work can no longer be trusted. AutoDev checks:

- the exact target repository path;
- the recorded issue artifact rather than refetching changed issue text;
- the recorded AutoDev branch;
- that the original base SHA is still an ancestor of the current branch;
- SHA-256 hashes for completed-stage artifacts, including the sanitized handoff and plan actually consumed downstream;
- the tracked and untracked working-tree paths recorded when a patch was applied;
- the current `git diff HEAD` hash;
- the recorded branch-head SHA after PR creation.

If a completed artifact, branch, or worktree changed externally, AutoDev refuses to resume instead of guessing which prior stage is still valid.

Area-reader checkpoint files are retained beneath the run output so reader, synthesis, and planner model calls can be replayed independently. Resume may rebuild deterministic repository maps/prompts, but completed model calls are reused only when their recorded artifacts still hash exactly.

### Patch and repair idempotency

Generated and applied patches are separate checkpoints. Before applying a saved patch AutoDev checks whether the reverse patch already applies; if so, it treats the patch as already present instead of applying it twice.

The manifest records the patch hash plus the resulting worktree hash and changed paths. A semantic repair invalidates the earlier deterministic-verification checkpoint before mutating the tree, so an interruption cannot accidentally reuse a pre-repair verification result.

A crash after AutoDev commits but before it records the PR is recoverable only when the worktree is clean and the branch still contains the expected AutoDev issue commit. Arbitrary extra commits or worktree changes are not accepted as equivalent state.

### Draft-PR idempotency

Before creating a draft PR, AutoDev asks GitHub whether the recorded branch already has a PR. An existing PR is recorded and reused rather than creating another one.

If AutoDev cannot determine whether a PR already exists, it fails closed rather than accepting duplicate-PR risk. Once `pr-created` is checkpointed, later resume requires the recorded branch-head SHA and a clean worktree.

### Plan-only resume

Interrupted `--mode plan-only` runs use the same manifest. Reader and synthesis checkpoints can be reused and a planner interruption resumes at `plan-created`. A completed plan-only manifest reports `Next stage: complete`; it is not silently converted into an implementation run.

### Recovery examples

Provider rate limit during implementation:

```text
1. python3 -m automation.run_real_issue --resume /tmp/autodev-run --status
2. Restore provider quota / wait for the rate limit to clear.
3. python3 -m automation.run_real_issue --resume /tmp/autodev-run
```

Planner model must change after planning:

```text
1. Edit or replace the provider profile.
2. Preview with --resume /tmp/autodev-run --status --invalidate-role planner
3. Resume with --resume /tmp/autodev-run --provider-config NEW.json --invalidate-role planner
```

Artifact drift:

```text
1. --status reports the changed artifact.
2. Restore the recorded artifact/worktree, or explicitly restart/invalidate the producing stage.
3. Resume only after the manifest and repository state validate again.
```

The manifest is state for one run directory. AutoDev does not add a database or global run registry.

## Scope boundaries

Provider-neutral role routing, semantic verification, optional Headroom compression, and resumable Python run manifests are implemented independently and compose through their existing metadata/artifact boundaries. The evaluation harness from issue #38 remains separate from the runtime workflow.

> Compatibility note: runs created by older AutoDev versions under `.codex-run` are not migrated automatically. Rename that directory to `.autodev-run` manually before resuming an old run.
