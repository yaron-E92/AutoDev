# Model roles

AutoDev can route each model-backed stage independently while retaining the original `reader` and `coder` configuration.

## Legacy configuration

Existing files remain valid:

```json
{
  "reader": {
    "provider": "command",
    "model": "reader-model",
    "command": "ollama run reader-model"
  },
  "coder": {
    "provider": "command",
    "model": "coder-model",
    "command": "ollama run coder-model"
  }
}
```

Legacy fallback mapping:

```text
reader       <- reader
synthesizer  <- reader
planner      <- coder
implementer  <- coder
fixer        <- coder
verifier     <- disabled
```

The existing `--reader-*`, `--coder-*`, `--reader`, and `--coder` CLI options continue to override roles that still use these fallbacks.

## Version 2 configuration

Use `version: 2` and a `roles` object to configure roles independently:

```json
{
  "version": 2,
  "roles": {
    "reader": { "provider": "command", "model": "reader-model", "command": "ollama run reader-model" },
    "synthesizer": { "provider": "command", "model": "synth-model", "command": "ollama run synth-model" },
    "planner": { "provider": "command", "model": "planner-model", "command": "ollama run planner-model" },
    "implementer": { "provider": "command", "model": "implementer-model", "command": "ollama run implementer-model" },
    "fixer": { "provider": "command", "model": "fixer-model", "command": "ollama run fixer-model" },
    "verifier": { "provider": "command", "model": "verifier-model", "command": "ollama run verifier-model" }
  }
}
```

An explicitly configured role wins over legacy CLI fallback values. The verifier role is used by semantic verification when that gate is enabled.

## Routing

```text
area reading           -> reader
cross-area synthesis   -> synthesizer
implementation plan    -> planner
initial patch           -> implementer
deterministic repair   -> fixer
semantic review        -> verifier
```

Use the role configuration through the supported platform issue-to-PR entrypoints. The wrappers resolve the profile in Python and pass model work through the shared provider boundary; examples are shown under **Existing issue-to-PR entrypoints** below.

The shared area runner also accepts `--provider-config`. Its legacy `--synthesizer` option remains a model-only compatibility override when the synthesizer role is not independently configured.

## Metadata

`provider-metadata.json` records the safe static configuration for every role. `model-invocations.json` records each call's role, provider, model, timeout, attempt, start/end timestamps, elapsed time, success or failure, and optional Headroom compression metadata. Secret values, header values, and full environment variables are not written.

## Opt-in Ollama Cloud profile

`examples/providers/ollama-cloud-nemotron-minimax.json` maps the roles as follows:

```text
reader       -> nemotron-3-super:cloud
synthesizer  -> nemotron-3-super:cloud
planner      -> nemotron-3-super:cloud
implementer  -> minimax-m3:cloud
fixer        -> minimax-m3:cloud
verifier     -> nemotron-3-super:cloud
```

This profile is opt-in. Ollama Cloud availability and usage limits depend on the signed-in account and plan. AutoDev does not guarantee free access to either model and does not silently substitute another model.

Ollama `0.12.0` is the minimum supported version because that release introduced cloud models. Current Ollama documentation requires signing in and recommends pulling a cloud model before running it:

- <https://github.com/ollama/ollama/releases/tag/v0.12.0>
- <https://docs.ollama.com/cloud>
- <https://docs.ollama.com/api/authentication>

### Install or update Ollama

On Windows, Ollama downloads updates automatically. Use the taskbar application's **Restart to update** action, or install the current `OllamaSetup.exe` from the official download page.

On Linux, install or update with:

```bash
curl -fsSL https://ollama.com/install.sh | sh
```

Confirm the installed version and sign in:

```text
ollama --version
ollama signin
```

Ensure the local Ollama service is running. Windows normally starts it in the background. On Linux, use the installed service or run `ollama serve`.

### Validate access

Use Ollama's normal CLI to confirm the configured cloud models are available before running AutoDev. AutoDev no longer ships a separate Ollama-specific preflight command; provider/runtime failures are handled by the canonical execution path.

### Use the profile

After the preflight succeeds, pass the same profile to the supported platform issue-to-PR entrypoints shown below. The profile remains the source of truth for role/provider selection during that run.

On PowerShell, replace each trailing `\` with a backtick, or place the command on one line.

To replace either model, copy the profile and change the relevant `model` fields. The command provider derives the cross-platform `ollama run <model>` command automatically, so no machine-specific executable path is needed.

## Role-specific prompt policies

AutoDev applies a concise, role-specific adaptation of Ponytail directly at its provider boundary. It does not require the Ponytail Codex plugin, hooks, MCP server, or a network fetch during a run.

Default mapping:

```text
reader       -> off
synthesizer  -> lite
planner      -> lite
implementer  -> full
fixer        -> full
verifier     -> review
```

The reader remains `off`, so factual repository inspection receives no YAGNI, deletion, reuse, or smallest-diff pressure. `lite` favors existing behavior and the smallest complete approach while preserving requirements and uncertainty. `full` adds comprehension-first reuse and root-cause guidance with explicit safety carve-outs. `review` asks the verifier to identify unnecessary scope or abstractions and missing requirements or safeguards without rewriting the solution.

Configure policies in the same provider file with the top-level `prompt_policy` object:

```json
{
  "version": 2,
  "roles": {
    "reader": { "provider": "command", "model": "reader-model" },
    "synthesizer": { "provider": "command", "model": "synth-model" },
    "planner": { "provider": "command", "model": "planner-model" },
    "implementer": { "provider": "command", "model": "implementer-model" },
    "fixer": { "provider": "command", "model": "fixer-model" },
    "verifier": { "provider": "command", "model": "verifier-model" }
  },
  "prompt_policy": {
    "enabled": true,
    "roles": {
      "planner": "off",
      "implementer": "lite"
    }
  }
}
```

Omitted role entries keep the stable defaults. Disable all policy injection with:

```json
{
  "prompt_policy": {
    "enabled": false
  }
}
```

Supported modes are `off`, `lite`, `full`, and `review`. AutoDev inserts the policy before issue and repository evidence. Exact output contracts remain last, including `BEGIN_UNIFIED_DIFF`, `END_UNIFIED_DIFF`, `NO_CHANGES_REQUIRED`, and the semantic verifier JSON schema.

Static metadata records the resolved mapping and source version beneath `prompt_policy` in `provider-metadata.json` and area-reader summaries. Every model-call record includes `prompt_policy_mode`, `prompt_policy_version`, source version, and source commit.

### Source and attribution

The adaptation is pinned to Ponytail `v4.8.4`, commit `bc9ee949d5f439e8b9f3bb92c6d6d3d1e6ebd324`, specifically its comprehension-first, reuse-before-writing, root-cause, and safety-carve-out principles:

- <https://github.com/DietrichGebert/ponytail/releases/tag/v4.8.4>
- <https://github.com/DietrichGebert/ponytail/blob/bc9ee949d5f439e8b9f3bb92c6d6d3d1e6ebd324/.agents/rules/ponytail.md>

The upstream MIT license is retained at `third_party/ponytail/LICENSE`.

This is an AutoDev-native adaptation rather than a verbatim runtime copy. Differences from the Ponytail plugin are intentional:

- the policy is selected by AutoDev model role;
- reader minimization guidance is disabled;
- verifier guidance is review-only;
- there is no `ultra` mode;
- AutoDev's explicit issue requirements, safety rules, and machine-readable output contracts always take precedence.

## Provider-neutral transports

Version-2 role entries may use `transport` (preferred) or the backward-compatible `provider` field. Supported generic transports are:

```text
command
openai-compatible-chat-completions
openai-compatible-responses
mock
```

The aliases `chat-completions`, `openai-compatible`, `responses`, and `ollama` remain accepted. `ollama` is normalized to the command transport and generates `ollama run <model>` when a command is omitted.

A role may configure:

```json
{
  "transport": "openai-compatible-chat-completions",
  "model": "provider/model",
  "base_url": "https://provider.example/v1",
  "api_key_env": "PROVIDER_API_KEY",
  "timeout_seconds": 1800,
  "headers": {
    "X-Title": "AutoDev"
  },
  "request_options": {
    "temperature": 0.1
  },
  "output_limit": 4096,
  "profile_name": "optional-name"
}
```

API-key values are read only from the named environment variable. Safe metadata records the environment-variable name and header names, never resolved secrets or header values. `Authorization`, `X-Api-Key`, cookies, and other sensitive headers cannot be placed in committed profile headers.

Chat Completions omits `max_tokens` by default. Responses omits `max_output_tokens` by default. Either field is added only when `output_limit` is explicitly configured. Provider response text is passed alone to the planner, unified-diff, and verifier parsers; usage, reported cost, model, timing, retries, sanitized failure classification, and compression telemetry are written separately to `model-invocations.json`.

### Optional Headroom compression

Version-2 profiles may add a top-level `headroom` object. Headroom applies only to OpenAI-compatible HTTP transports; command providers remain unchanged.

```json
{
  "headroom": {
    "enabled": true,
    "proxy_url": "http://127.0.0.1:8787/v1",
    "mode": "lossless",
    "output_shaping": false,
    "fail_open": true,
    "roles": {
      "verifier": { "enabled": false }
    }
  }
}
```

Global settings are inherited by each role and role entries override them. Verifier compression defaults to disabled unless explicitly enabled. AutoDev compresses only known evidence sections through Headroom's compression-only endpoint, then proxy-routes the completed request with automatic recompression bypassed so issue requirements, Ponytail policy, branch/file constraints, patch markers, `NO_CHANGES_REQUIRED`, and verifier JSON contracts remain untouched.

See `docs/headroom.md` for installation, Windows/Linux startup, routing/fail-open behavior, telemetry, dashboard checks, Ollama HTTP usage, and compressed-versus-uncompressed comparison.

### Checked-in profiles

- `examples/providers/groq-openrouter-free.json`: Groq for reader, synthesizer, planner, and verifier; configurable OpenRouter `:free` model for implementer and fixer.
- `examples/providers/groq-openrouter-free-headroom.json`: the same mixed mapping with optional Headroom compression for eligible HTTP-role evidence and verifier compression disabled.
- `examples/providers/ollama-local-all-roles.json`: local Ollama commands for all six roles.
- `examples/providers/ollama-openai-compatible-headroom.json`: local Ollama through its OpenAI-compatible HTTP API with Headroom enabled.
- `examples/providers/codex-command-profile.json`: Codex CLI profiles through the generic command transport. `direct_edit: true` is used only for implementer and fixer.
- `examples/providers/ollama-cloud-nemotron-minimax.json`: the existing opt-in Ollama Cloud mapping.

Before using the mixed profile, replace `REPLACE_WITH_OPENROUTER_MODEL:free` with a currently accessible OpenRouter model whose identifier still ends in `:free`, then set credentials without committing them:

```powershell
$env:GROQ_API_KEY = "..."
$env:OPENROUTER_API_KEY = "..."
```

```bash
export GROQ_API_KEY="..."
export OPENROUTER_API_KEY="..."
```

When `free_only` is true, every configured model and fallback must end in `:free`, and AutoDev sends `provider.allow_fallbacks: false`. It never substitutes a paid model or automatic paid route. Configure additional fallbacks only through `fallback_models`, and keep every entry explicitly free.

### Provider-neutral preflight

The preflight validates profile structure, required environment variables, command executables, endpoint reachability, and model visibility when `/models` is available. It does not select an issue, change labels, create branches, or contact GitHub. Headroom itself is fail-open by default and is checked independently with its `/health` endpoint.

```powershell
python -m automation.provider_preflight `
  --provider-profile examples/providers/groq-openrouter-free.json `
  --out .autodev-run/provider-preflight.json
```

```bash
python3 -m automation.provider_preflight \
  --provider-profile examples/providers/groq-openrouter-free.json \
  --out .autodev-run/provider-preflight.json
```

Failures are classified without persisting provider response bodies or secrets: missing credentials, unavailable command, authentication failure (`401`), payment/plan required (`402`), endpoint/model not found (`404`), rate limit/quota exhausted (`429`), malformed response, timeout, or transport error.

### Existing issue-to-PR entrypoints

Windows PowerShell:

```powershell
.\scripts\run-real-issue.ps1 `
  -WorkingDirectory C:\src\target-repo `
  -Mode Preflight `
  -ProviderProfile .\examples\providers\groq-openrouter-free.json

.\scripts\run-real-issue.ps1 `
  -WorkingDirectory C:\src\target-repo `
  -Issue 46 `
  -ProviderProfile .\examples\providers\groq-openrouter-free.json
```

Linux:

```bash
linux/scripts/issue-to-pr-cycle.sh \
  --env linux/config.env \
  --mode Preflight \
  --provider-profile examples/providers/groq-openrouter-free.json

linux/scripts/issue-to-pr-cycle.sh \
  --env linux/config.env \
  --issue 46 \
  --provider-profile examples/providers/groq-openrouter-free.json
```

Both wrappers pass role and profile references to `automation.prompt_runner`; provider validation, request construction, model invocation, failure classification, compression telemetry, and response parsing live in Python. Legacy planner/agent provider, model, and command flags remain accepted and are resolved by Python. Codex Desktop is optional and is not needed for a headless run.

Semantic verification, resumable manifests, and Headroom compression are integrated layers above the shared role/provider transport.
