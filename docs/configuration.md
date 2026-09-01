# AutoDev user configuration

AutoDev has a machine/user-local configuration layer for defaults that should follow the user or machine rather than a repository. It is separate from repository-owned `.autodev/` policy and from OpenCode's own `opencode.json` / `opencode.jsonc`.

Use it for reusable model profiles, per-repository profile selection, the default role runtime, and scheduler defaults.

## Configuration file location

AutoDev resolves the user configuration path in this order:

1. `AUTODEV_USER_CONFIG`, when explicitly set;
2. `$XDG_CONFIG_HOME/autodev/config.json` on systems using XDG;
3. `%APPDATA%\AutoDev\config.json` on Windows;
4. `~/.config/autodev/config.json` otherwise.

Inspect the exact path on the current machine with:

```text
autodev config path
```

Show the current file contents with:

```text
autodev config show
```

No user configuration file is required. When the file does not exist, AutoDev keeps its built-in defaults: role runtime `opencode`, no AutoDev model profile selected, and scheduler cadence 15 minutes.

A minimal valid file equivalent to those defaults is:

```json
{
  "version": 1,
  "role_runtime": "opencode",
  "scheduler": {
    "cadence_minutes": 15
  }
}
```

A copyable version is shipped as [`../examples/autodev/config.default.json`](../examples/autodev/config.default.json). A fuller profile example is [`../examples/autodev/config.profiles.example.json`](../examples/autodev/config.profiles.example.json).

## Full schema

The current schema version is `1`. Supported top-level fields are:

| Field | Meaning |
| --- | --- |
| `version` | AutoDev user-config schema version. Current value: `1`. |
| `role_runtime` | User-wide default role runtime. `opencode` is the built-in default. |
| `active_model_profile` | User-wide named AutoDev model profile. |
| `model_profiles` | Named mappings for the six AutoDev workflow roles. |
| `repositories` | Per-GitHub-repository overrides keyed by canonical `OWNER/REPO`. |
| `scheduler.cadence_minutes` | User-wide default scheduler wake cadence. |

AutoDev writes this file atomically through the `autodev config` commands.

## Model profiles

A model profile maps any subset of AutoDev's six workflow roles:

```text
reader
synthesizer
planner
implementer
fixer
verifier
```

Every model route uses OpenCode's `provider/model` syntax.

Example mixed local/OpenAI profile:

```json
{
  "version": 1,
  "active_model_profile": "mixed",
  "model_profiles": {
    "mixed": {
      "reader": "ollama/gpt-oss:20b-autodev",
      "synthesizer": "ollama/gpt-oss:20b-autodev",
      "planner": "openai/gpt-5.6-terra",
      "implementer": "openai/gpt-5.6-sol",
      "fixer": "openai/gpt-5.6-sol",
      "verifier": "openai/gpt-5.6-terra"
    }
  }
}
```

Create or replace that profile through the CLI:

```text
autodev config profile set mixed \
  reader=ollama/gpt-oss:20b-autodev \
  synthesizer=ollama/gpt-oss:20b-autodev \
  planner=openai/gpt-5.6-terra \
  implementer=openai/gpt-5.6-sol \
  fixer=openai/gpt-5.6-sol \
  verifier=openai/gpt-5.6-terra

autodev config profile use mixed
```

Profile entries are deliberately machine-local. Do not commit this user config to target repositories merely to share local routing preferences.

### Local-only example

```json
{
  "model_profiles": {
    "local": {
      "reader": "ollama/gpt-oss:20b-autodev",
      "synthesizer": "ollama/gpt-oss:20b-autodev",
      "planner": "ollama/gpt-oss:20b-autodev",
      "implementer": "ollama/gpt-oss:20b-autodev",
      "fixer": "ollama/gpt-oss:20b-autodev",
      "verifier": "ollama/gpt-oss:20b-autodev"
    }
  }
}
```

### Local + OpenRouter example

OpenRouter model IDs vary by account/provider availability. Use the exact route exposed by your OpenCode installation:

```json
{
  "model_profiles": {
    "local-openrouter": {
      "reader": "ollama/gpt-oss:20b-autodev",
      "synthesizer": "ollama/gpt-oss:20b-autodev",
      "planner": "openrouter/provider/planner-model",
      "implementer": "openrouter/provider/implementer-model",
      "fixer": "openrouter/provider/implementer-model",
      "verifier": "openrouter/provider/verifier-model"
    }
  }
}
```

Replace illustrative model IDs with routes that `opencode models` / your provider configuration actually exposes.

## Per-repository profile selection

A repository-specific selection belongs in the same user config, keyed by canonical GitHub identity:

```json
{
  "active_model_profile": "local",
  "repositories": {
    "yaron-E92/PHOODAB": {
      "model_profile": "mixed"
    }
  }
}
```

This means repositories normally use `local`, while `yaron-E92/PHOODAB` uses `mixed`; nothing is written into PHOODAB merely to express that machine-local choice.

Set it from inside the repository with:

```text
autodev config profile use mixed --repo .
```

Clear the repository override and return to the user-wide profile with:

```text
autodev config profile clear --repo .
```

AutoDev normalizes SSH and HTTPS GitHub remotes to the same `OWNER/REPO` identity, so `git@github.com:owner/repo.git` and `https://github.com/owner/repo.git` address the same override.

## Model-routing precedence

For an AutoDev workflow role, the effective model is resolved in this order:

1. an explicit `agent.autodev-<role>.model` in the repository's effective `opencode.json` / `opencode.jsonc`;
2. a repository-selected AutoDev model profile from the user config;
3. the user-wide `active_model_profile`;
4. existing OpenCode coordinator/global/default inheritance;
5. unresolved, which is a deterministic configuration error where a concrete route is required.

An AutoDev profile therefore **fills inherited roles**; it does not silently override an explicit repository-owned OpenCode role model.

The OpenCode frontend also has an `autodev-coordinator` agent. The AutoDev user model-profile schema covers the six workflow roles only. Configure an explicit coordinator model in `opencode.json(c)` if you want to override the OpenCode frontend coordinator separately.

Inspect the final resolution and source for every role with:

```text
autodev models
```

## Runtime precedence

Role-runtime selection is independent from model-profile selection. Runtime precedence is:

1. explicit `--runtime`;
2. `AUTODEV_ROLE_RUNTIME`;
3. repository `.autodev/config.json` `role_runtime`;
4. user AutoDev configuration `role_runtime`;
5. built-in `opencode`.

A model profile is currently meaningful for the OpenCode runtime.

## Scheduler defaults

Set the user-wide default scheduler cadence with:

```text
autodev config scheduler-cadence 15
```

Show the effective configured/default cadence with:

```text
autodev config scheduler-cadence
```

An explicit scheduler-install value wins over the user default:

```text
autodev scheduler install --cadence-minutes 30
```

If neither is configured explicitly, the built-in cadence remains 15 minutes.

Scheduler installation uses the same effective AutoDev/OpenCode model resolution as interactive execution and requires concrete headless-safe routes before registering the native task. See [`scheduler.md`](scheduler.md).

## Credentials and privacy

Do **not** put API keys, access tokens, SSH private keys, passwords, or privacy-consent grants in AutoDev user configuration.

Model profiles contain route names only. Authentication remains in the provider/OpenCode/GitHub credential mechanisms intended for those services.

Repository privacy policy remains under `.autodev/privacy.json`. Persistent privacy grants are stored separately as user-local AutoDev privacy state and are not part of this configuration file. See [`privacy.md`](privacy.md).

## OpenCode configuration vs AutoDev configuration

Use AutoDev user configuration when the choice is machine/user-local and should be reusable across repositories.

Use repository `opencode.json(c)` when the repository intentionally owns an explicit OpenCode setting or a specific AutoDev agent model mapping that should travel with the repository.

This separation avoids both extremes: committing every developer's local model choices, or requiring every dedicated scheduler worker to inherit an untracked `opencode.jsonc`.
