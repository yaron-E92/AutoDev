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
