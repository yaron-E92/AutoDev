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

An explicitly configured role wins over legacy CLI fallback values. The verifier configuration is resolved and reported but is not invoked until semantic verification is implemented.

## Routing

```text
area reading           -> reader
cross-area synthesis   -> synthesizer
implementation plan    -> planner
initial patch           -> implementer
deterministic repair   -> fixer
semantic review        -> verifier (reserved)
```

Use the configuration with the operational runner:

```text
python -m automation.run_real_issue \
  --repo /path/to/repo \
  --github-repo OWNER/REPO \
  --issue 32 \
  --mode plan-only \
  --provider-config providers.json \
  --out /tmp/autodev-run
```

The shared area runner also accepts `--provider-config`. Its legacy `--synthesizer` option remains a model-only compatibility override when the synthesizer role is not independently configured.

## Metadata

`provider-metadata.json` records the safe static configuration for every role. `model-invocations.json` records each call's role, provider, model, timeout, attempt, start/end timestamps, elapsed time, and success or failure. Secret values and full environment variables are not written.

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

### Run the preflight

The preflight checks the executable, minimum version, local API, sign-in/access status, and each unique cloud model. It uses `ollama pull` and does not select an issue, change labels, create a branch, modify a target repository, or contact GitHub.

Linux:

```bash
python -m automation.ollama_cloud_preflight \
  --profile examples/providers/ollama-cloud-nemotron-minimax.json \
  --out .autodev-run/ollama-cloud-preflight.json
```

Windows PowerShell:

```powershell
python -m automation.ollama_cloud_preflight `
  --profile examples/providers/ollama-cloud-nemotron-minimax.json `
  --out .autodev-run/ollama-cloud-preflight.json
```

The JSON result records the selected profile path, Ollama version, role/model mapping, local-service result, and per-model access result. Authentication tokens and environment-variable values are not recorded.

Failure messages distinguish:

```text
missing Ollama
outdated Ollama
unreachable local service
sign-in required
plan upgrade required
generic model access failure
```

### Use the profile

```text
python -m automation.run_real_issue \
  --repo /path/to/repo \
  --github-repo OWNER/REPO \
  --issue 33 \
  --mode plan-only \
  --provider-config examples/providers/ollama-cloud-nemotron-minimax.json \
  --out /tmp/autodev-run
```

On PowerShell, replace each trailing `\` with a backtick, or place the command on one line.

To replace either model, copy the profile and change the relevant `model` fields. The command provider derives the cross-platform `ollama run <model>` command automatically, so no machine-specific executable path is needed.
