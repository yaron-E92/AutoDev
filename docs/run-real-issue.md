# Script-based issue-to-PR workflow

The preferred real-issue workflow keeps deterministic GitHub and repository stages in the existing Windows/Linux scripts while delegating every configured model role to the shared Python provider layer.

```text
issue selection and state
  -> reader
  -> synthesizer
  -> planner
  -> implementer
  -> local/CI checks
  -> fixer when needed
  -> verifier
  -> ready-for-review state
```

The six roles are independently configured in one version-2 provider profile. The shell scripts pass only role, profile, prompt, output, and telemetry paths to `automation.prompt_runner`.

## Checked-in profiles

```text
examples/providers/groq-openrouter-free.json
examples/providers/ollama-local-all-roles.json
examples/providers/ollama-cloud-nemotron-minimax.json
examples/providers/codex-command-profile.json
```

The mixed Groq/OpenRouter example intentionally contains `REPLACE_WITH_OPENROUTER_MODEL:free`. Copy the file and replace that value with a currently accessible model that still ends in `:free`.

Set credentials in the environment, not in JSON:

```powershell
$env:GROQ_API_KEY = "..."
$env:OPENROUTER_API_KEY = "..."
```

```bash
export GROQ_API_KEY="..."
export OPENROUTER_API_KEY="..."
```

## Provider-neutral preflight

Preflight checks configuration, required environment variables, command executables, endpoint reachability, and model visibility without selecting an issue or modifying GitHub/repository state.

Windows:

```powershell
scripts\run-real-issue.ps1 `
  -WorkingDirectory C:\src\target-repo `
  -Mode Preflight `
  -ProviderProfile C:\src\AutoDev\examples\providers\groq-openrouter-free.json
```

Linux:

```bash
scripts/run-real-issue.sh \
  --env ~/automation/state/PROJECT.env \
  --mode Preflight \
  --provider-profile examples/providers/groq-openrouter-free.json
```

The default result is `.codex-run/provider-preflight.json`. It contains safe profile, role, transport, model, and failure-classification data only.

## Run one issue

Windows:

```powershell
scripts\run-real-issue.ps1 `
  -WorkingDirectory C:\src\target-repo `
  -Username owner `
  -Repo repository `
  -Issue 46 `
  -ProviderProfile C:\src\AutoDev\examples\providers\groq-openrouter-free.json
```

Linux:

```bash
scripts/run-real-issue.sh \
  --env ~/automation/state/PROJECT.env \
  --mode Run \
  --owner owner \
  --repo repository \
  --issue 46 \
  --provider-profile examples/providers/groq-openrouter-free.json
```

Omit the issue number to select the next eligible `autodev:ready` issue. Literal task text remains supported through description flags.

## Transport contract

Supported transport names are:

```text
command
openai-compatible-chat-completions
openai-compatible-responses
mock
```

Backward-compatible aliases remain accepted:

```text
ollama
chat-completions
openai-compatible
responses
```

`ollama` is implemented through the command transport. A model-only legacy override generates `ollama run <model>` in Python.

For HTTP transports, configure `base_url`, `model`, and optionally `api_key_env`, allowlisted `headers`, `request_options`, and `output_limit`. API-key values and sensitive headers are never written to metadata.

Chat Completions omits `max_tokens` unless `output_limit` is explicit. Responses omits `max_output_tokens` unless `output_limit` is explicit.

## Text providers versus direct-edit commands

HTTP providers and normal command providers return pure text. Implementer/fixer text must satisfy the existing patch contract:

```text
NO_CHANGES_REQUIRED
```

or:

```text
BEGIN_UNIFIED_DIFF
<applicable unified diff>
END_UNIFIED_DIFF
```

Implementer responses may additionally provide:

```text
COMMIT_MESSAGE: concise imperative message
```

Command profiles may set `direct_edit: true` for implementer/fixer. In that mode the command edits the workspace directly; the implementer must also write the requested commit-message file. The checked-in Codex command profile uses direct-edit only for these two roles.

## OpenRouter free-only safety

When `free_only` is true:

- the primary model and every configured fallback must end in `:free`;
- AutoDev sends `provider.allow_fallbacks: false`;
- a non-free model causes configuration failure;
- AutoDev does not silently replace the requested model with a paid route.

The example does not claim that any specific free model is permanently available.

## Telemetry and errors

`model-invocations.json` is separate from model response text. The parsers see only the response text. Safe records may contain:

```text
profile name
role
transport
requested and provider-reported model
timeout
timestamps and elapsed seconds
attempt/retry count
numeric usage and reported cost
HTTP status and retry-after
sanitized failure classification
prompt-policy mode/version
```

Authentication values, authorization headers, provider response bodies, exception diagnostics, and arbitrary environment values are not persisted.

Failures distinguish invalid configuration, missing credentials, unavailable command, authentication failure, payment/plan required, not found, timeout, rate limit/quota exhaustion, malformed response, and transport failure.

## Legacy flags

The existing planner/agent provider, model, and command flags remain accepted by Windows and Linux wrappers. The wrappers no longer restrict or interpret provider names; Python resolves and validates the overrides. When no profile or provider/model override is supplied, the existing direct-agent command path remains available.

Repair calls are normalized to the `fixer` role.

## Python operational runner

`automation.run_real_issue` continues to support the provider-config file directly:

```bash
python -m automation.run_real_issue \
  --repo . \
  --github-repo owner/repository \
  --issue 46 \
  --mode implement \
  --provider-config examples/providers/ollama-local-all-roles.json \
  --out .autodev-runs/issue-46
```

Its `plan-only`, `implement`, `pr`, skip, dry-run, label, branch, patch, and verification behavior is unchanged. Provider roles and prompt policies are shared with the script workflow.

See `docs/model-roles.md` for the complete version-2 schema and role-policy configuration.
