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
  --out .codex-run\provider-preflight.json
```

Linux:

```bash
python3 -m automation.provider_preflight \
  --provider-profile ~/autodev-groq-openrouter.json \
  --out .codex-run/provider-preflight.json
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
.codex-run/current/
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

`automation.run_real_issue` remains available for users who specifically want the older Python-owned planning, implementation, verification, and PR flow:

```bash
python3 -m automation.run_real_issue \
  --repo /path/to/repository \
  --github-repo OWNER/REPOSITORY \
  --issue 46 \
  --mode plan-only \
  --provider-config ~/autodev-groq-openrouter.json \
  --out /tmp/autodev-run
```

For the normal trusted Windows or Linux issue-to-PR workflow, use `scripts/run-real-issue.ps1` or `scripts/run-real-issue.sh` as documented above.

## Scope boundaries

This provider work does not implement:

- semantic verification policy from issue #35;
- Headroom compression from issue #36;
- resumable run manifests from issue #37;
- the evaluation harness from issue #38.

Those features remain separate from provider selection and invocation.
