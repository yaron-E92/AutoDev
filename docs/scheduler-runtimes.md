# Scheduler role runtimes

AutoDev treats the **role runtime** separately from the **model/provider route**. A runtime is the execution transport and integration layer (for example OpenCode). A route is the provider/model selected for an AutoDev role (for example `ollama/gpt-oss:20b-autodev`). Changing one does not implicitly change the other.

## Select and inspect the scheduler runtime

A new scheduler resolves its runtime in this order:

1. an explicit scheduler selection such as `autodev scheduler install --runtime opencode`;
2. the repository `role_runtime` setting;
3. the user-level `role_runtime` setting;
4. AutoDev's built-in default.

The ambient `AUTODEV_ROLE_RUNTIME` shell variable is deliberately **not** an install-time scheduler default. Interactive runs may use that override, but unattended work must be reproducible after the shell that installed the scheduler no longer exists.

Inspect the registered runtime with:

```text
autodev scheduler runtime
autodev scheduler runtime --json
```

The JSON form reports the persisted runtime name, selection source, safe runtime identity, effective role routes, and runtime fingerprint. `autodev scheduler status --json` also includes the registered runtime and fingerprint.

## Switch an installed scheduler

Use:

```text
autodev scheduler runtime set opencode
```

A switch is transactional at the scheduler-registration boundary. AutoDev first resolves the requested runtime, provisions its worker requirements, validates the runtime itself, validates every effective role route, and runs the existing headless privacy/consent preflight. Only after those checks pass does AutoDev replace the worker-local runtime identity and scheduler registration and refresh the scheduler backend.

If validation or backend refresh fails, AutoDev restores the previous valid registration and worker runtime identity. Runtime-owned files from a previous adapter may be retained when they are harmless; an adapter may remove only files for which it can prove scheduler ownership. Repository-owned tracked files are never deleted as part of runtime switching.

## Runtime adapter contract

Scheduler core does not know how a runtime is installed. A runtime adapter may implement these hooks:

- `provision_scheduler_worker`: materialize runtime-owned worker requirements;
- `validate_scheduler_worker`: prove the runtime and required role integration are discoverable;
- `validate_scheduler_routes`: prove each selected provider/model route is registered with the runtime without invoking a model when the runtime offers catalog discovery;
- `scheduler_runtime_environment`: return the small, non-secret environment required to reproduce unattended execution;
- `scheduler_runtime_identity`: return safe runtime/version/asset identity used for diagnostics and resume safety;
- `scheduler_runtime_routes`: return the effective role-to-provider/model mapping;
- `privacy_evidence`: produce the existing provider/runtime-specific evidence consumed by AutoDev's runtime-neutral privacy authorization layer.

A runtime may use `.opencode`, another directory, a user-level installation, or no repository-local assets at all. The scheduler lifecycle does not assume OpenCode paths.

The resulting state is persisted under the dedicated worker's `.git/autodev/scheduler-runtime.json` and copied into the scheduler registration. It contains no credentials. The runtime fingerprint includes the selected runtime, safe runtime identity, and effective routes. For scheduler runs it also participates in role snapshots, so a resume cannot silently reuse completed work after an execution-significant runtime/version/asset change.

## Reproducible scheduler environment

Scheduler backends use the runtime state captured during successful registration rather than relying on a later login shell. In particular, the registered `PATH` is used by systemd user services, cron entries, and Windows Task Scheduler actions. This fixes the common case where `autodev` itself is available through an absolute launcher but the selected role runtime executable is only discoverable through the install-time PATH.

Adapters must persist only non-secret reproducibility data. Credentials remain in the runtime/provider's normal credential mechanism. Runtime state must never copy API keys, tokens, passwords, cookies, or arbitrary secret-bearing shell configuration.

## OpenCode

The OpenCode scheduler adapter builds on the scheduler-owned agent/command provisioning introduced before this runtime abstraction. It still preserves tracked repository-owned `.opencode` files and only manages untracked files whose scheduler ownership is proven.

After agent/config discovery, the adapter validates all effective model routes against `opencode models`. A missing route fails before AutoDev starts model work with a diagnostic that the runtime model route is not registered/discoverable.

For a profile-selected local `ollama/<model>` route, AutoDev can generate the non-secret OpenCode provider registration needed by the dedicated worker, using OpenCode's OpenAI-compatible local Ollama provider shape and `http://localhost:11434/v1`. The generated overlay contains provider/model registration only; it does not contain credentials. This lets a dedicated scheduler worker use a local Ollama model even when the interactive source checkout previously depended on an untracked repository-local OpenCode provider block.

OpenRouter or other provider privacy controls remain owned by the existing privacy adapter. They are merged at invocation time with the scheduler's generated OpenCode configuration rather than replacing AutoDev's scheduler runtime state.
