# Optional Headroom compression

AutoDev can optionally compress model input evidence with a local Headroom proxy while keeping provider/model selection in the normal version-2 provider profile.

Headroom is not a Python dependency of AutoDev. If Headroom is disabled or absent, ordinary provider behavior is unchanged.

## Safety model

AutoDev does not send the completed prompt through Headroom's automatic compressor.

For an enabled HTTP role, AutoDev:

1. identifies only known evidence sections in the AutoDev prompt;
2. sends those evidence sections to Headroom's `/v1/compress` endpoint;
3. reassembles the prompt with protected sections byte-for-byte unchanged;
4. sends the final request through the Headroom OpenAI-compatible proxy with the original upstream URL in `X-Headroom-Base-Url`;
5. sends `X-Headroom-Bypass: true` on that final request so the proxy does not recompress the completed prompt or reshape model output.

Protected content includes issue requirements, role/safety instructions, Ponytail policy, branch/file constraints, patch markers, `NO_CHANGES_REQUIRED`, and semantic-verifier JSON contracts. Reader/synthesizer repository bundles, plans, diffs, verification evidence, and similar large context are eligible where the prompt has a known boundary.

If a role's prompt shape is unknown, AutoDev routes it without evidence compression instead of guessing a boundary.

## Install Headroom

Install the proxy extra. Headroom recommends an isolated CLI environment when `uv` is available, but normal `pip` installation also works.

Windows PowerShell:

```powershell
py -m pip install "headroom-ai[proxy]"
headroom --version
```

Linux:

```bash
python3 -m pip install --user "headroom-ai[proxy]"
headroom --version
```

Alternative with `uv`:

```text
uv tool install --python 3.13 "headroom-ai[proxy]"
```

## Start the proxy in lossless mode

AutoDev's checked-in Headroom profiles require the Headroom `--lossless` no-CCR mode. Output shaping must remain disabled.

Windows PowerShell:

```powershell
$env:HEADROOM_OUTPUT_SHAPER = "0"
headroom proxy --lossless --host 127.0.0.1 --port 8787
```

Linux:

```bash
export HEADROOM_OUTPUT_SHAPER=0
headroom proxy --lossless --host 127.0.0.1 --port 8787
```

The default AutoDev proxy URL is:

```text
http://127.0.0.1:8787/v1
```

Check the running proxy separately:

```text
http://127.0.0.1:8787/health
http://127.0.0.1:8787/stats
http://127.0.0.1:8787/stats-history
http://127.0.0.1:8787/dashboard
```

Headroom's own dashboard/history is supplemental. AutoDev records only metrics that Headroom actually returns for the compression request; it does not estimate missing token or cost savings.

## Mixed Groq + OpenRouter profile

Use:

```text
examples/providers/groq-openrouter-free-headroom.json
```

Copy it and replace:

```text
REPLACE_WITH_OPENROUTER_MODEL:free
```

with an OpenRouter model identifier that still ends in `:free`.

Set the normal provider credentials:

```powershell
$env:GROQ_API_KEY = "..."
$env:OPENROUTER_API_KEY = "..."
```

```bash
export GROQ_API_KEY="..."
export OPENROUTER_API_KEY="..."
```

Headroom does not own those credentials and AutoDev does not store their values. The original upstream model, API-key environment variable, request options, and `free_only` settings are retained behind the proxy. OpenRouter roles still force `provider.allow_fallbacks: false` when `free_only` is true.

The example leaves verifier compression disabled while semantic verification itself remains enabled.

## Ollama through its OpenAI-compatible HTTP API

The existing `ollama-local-all-roles.json` uses the command transport, so Headroom intentionally does not wrap it.

To use Headroom with local Ollama, use the HTTP example instead:

```text
examples/providers/ollama-openai-compatible-headroom.json
```

It points the normal upstream roles at:

```text
http://127.0.0.1:11434/v1
```

and routes final OpenAI-compatible requests through the Headroom proxy. The Ollama service and configured models must already be available.

## Run AutoDev

Use the Headroom-enabled profile exactly like any other provider profile.

Windows:

```powershell
scripts\run-real-issue.ps1 `
  -Mode Run `
  -WorkingDirectory "C:\repos\TARGET_REPOSITORY" `
  -Username OWNER `
  -Repo REPOSITORY `
  -Issue 36 `
  -ProviderProfile .\examples\providers\groq-openrouter-free-headroom.json
```

Linux:

```bash
~/repos/AutoDev/scripts/run-real-issue.sh \
  --env ~/automation/state/PROJECT.env \
  --mode Run \
  --owner OWNER \
  --repo REPOSITORY \
  --issue 36 \
  --provider-profile ~/repos/AutoDev/examples/providers/groq-openrouter-free-headroom.json
```

The PowerShell and Bash orchestrators do not contain Headroom-specific provider logic. Python resolves the profile and routing.

## Configuration

Headroom is a top-level version-2 provider-profile option:

```json
{
  "version": 2,
  "headroom": {
    "enabled": true,
    "proxy_url": "http://127.0.0.1:8787/v1",
    "mode": "lossless",
    "output_shaping": false,
    "fail_open": true,
    "roles": {
      "reader": { "enabled": true },
      "synthesizer": { "enabled": true },
      "planner": { "enabled": true },
      "implementer": { "enabled": true },
      "fixer": { "enabled": true },
      "verifier": { "enabled": false }
    }
  },
  "roles": {}
}
```

Role entries override the global Headroom settings. If `verifier.enabled` is omitted, verifier compression defaults to disabled.

Supported AutoDev Headroom settings are:

```text
enabled
proxy_url
mode                # must be lossless
output_shaping      # must be false
fail_open
roles
```

## Fail-open behavior

`fail_open: true` is the default.

If the compression-only request fails before the model provider is called, AutoDev records a compression warning and sends the original uncompressed prompt to the exact original upstream configuration.

If the Headroom proxy itself is unreachable for the final request, AutoDev retries the original prompt directly against the exact original upstream configuration.

Normal provider failures are not converted into Headroom failures. Authentication (`401`), payment/plan (`402`), model/endpoint (`404`), rate-limit (`429`), and other upstream HTTP failures retain the existing provider classifications and are not silently retried against another model or provider.

## Telemetry

Every model invocation continues to write provider metadata separately from model text in `model-invocations.json`.

When Headroom is enabled, the call record includes a `compression` object with safe fields such as:

```text
status
enabled
mode
proxy_url
upstream_base_url
elapsed_compression_seconds
characters_before
characters_after
evidence_characters_before
evidence_characters_after
original_prompt_sha256
effective_prompt_sha256
fail_open_used
```

If Headroom reports token counts or a compression ratio, AutoDev records those exact values. Missing values remain absent.

When provider-backed execution enables debug artifacts, AutoDev also writes concise records beneath:

```text
compression/reader-attempt-0.json
compression/synthesizer-attempt-0.json
compression/planner-attempt-0.json
compression/implementer-attempt-0.json
compression/fixer-attempt-1.json
compression/verifier-attempt-0.json
```

The artifacts contain hashes and safe metadata, not full prompt contents, authorization headers, API keys, or environment-variable values.

## Disable compression

Use the corresponding non-Headroom profile, or set:

```json
"headroom": {
  "enabled": false
}
```

A role can be disabled independently:

```json
"headroom": {
  "enabled": true,
  "roles": {
    "verifier": { "enabled": false }
  }
}
```

## Command-provider limitation

Headroom is supported only for `openai-compatible-chat-completions` and `openai-compatible-responses` transports.

Generic command transports are deliberately unchanged. This includes the checked-in Codex command profile and the normal Ollama `ollama run <model>` profile. Use an OpenAI-compatible HTTP endpoint when you want Headroom compression for a local provider.

## Compressed versus uncompressed comparison

To compare behavior, run the same task once with a Headroom-enabled copy of a profile and once with `headroom.enabled: false`. Compare:

```text
model-invocations.json
compression/*.json          # when --debug-artifacts is enabled
Headroom /stats or dashboard
semantic verification result
```

Do not compare by changing provider/model at the same time; the purpose is to isolate compression behavior.
