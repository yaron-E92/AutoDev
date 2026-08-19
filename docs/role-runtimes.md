# AutoDev role runtimes

AutoDev's deterministic Python coordinator owns workflow ordering, durable state, verification, repair budgets, shipment, and resume decisions. Model-heavy roles execute through a separate **role runtime** boundary.

```text
AutoDev coordinator/state machine
        ↓
role runtime
        ↓
role/model execution mechanism
```

The runtime does not decide which stage runs next and does not make artifact acceptance authoritative. Python validates every role output through the existing AutoDev role contract before a stage can advance.

## Default runtime

`opencode` is the default runtime. With no additional configuration, AutoDev continues to execute roles through the installed OpenCode agents:

```text
opencode run --agent autodev-reader ...
opencode run --agent autodev-synthesizer ...
opencode run --agent autodev-planner ...
opencode run --agent autodev-implementer ...
opencode run --agent autodev-fixer ...
opencode run --agent autodev-verifier ...
```

OpenCode role/model mappings remain owned by effective `opencode.json` / `opencode.jsonc` configuration. AutoDev does not copy those mappings into `.autodev`.

## Selecting a runtime

Selection uses this precedence, from highest to lowest:

1. explicit per-run `--runtime`;
2. `AUTODEV_ROLE_RUNTIME` environment variable;
3. repository `.autodev/config.json` field `role_runtime`;
4. user AutoDev configuration field `role_runtime`;
5. default `opencode`.

Example repository or user configuration:

```json
{
  "role_runtime": "opencode"
}
```

The user configuration is resolved from `AUTODEV_USER_CONFIG` when set. Otherwise AutoDev uses `$XDG_CONFIG_HOME/autodev/config.json` when available, `%APPDATA%/AutoDev/config.json` on Windows, or `~/.config/autodev/config.json` as the portable fallback. Repository configuration intentionally overrides user configuration.

Example explicit invocation through the current compatibility frontend:

```text
python3 .opencode/autodev.py coordinate --arguments "123" --runtime opencode
```

The first-class `autodev` CLI from the installation work may expose the same option without changing this selection contract.

An explicitly selected unknown runtime fails before model-heavy role execution. AutoDev never silently falls back to another runtime.

## Runtime identity and resume

The selected runtime is recorded as safe metadata in run diagnostics and the durable run manifest. Each role snapshot includes the runtime identity in its execution fingerprint.

OpenCode already fingerprinted `transport: opencode` before the runtime abstraction existed. The OpenCode runtime deliberately preserves that exact fingerprint shape, so installing this change does not invalidate otherwise-compatible in-progress OpenCode runs.

Consequently, changing to a different runtime for a role whose output has already been completed follows the same protection as changing an execution-affecting model configuration: resume is refused until the operator explicitly invalidates the affected role, for example with the existing `--invalidate-role` mechanism. A rejected runtime switch does not overwrite the previous manifest identity.

## Runtime contract

A runtime supplies two things to the coordinator:

- safe execution snapshots for role fingerprinting/resume validation;
- a bounded invocation result containing runtime, role, termination/exit state, elapsed time, safe model identity when available, and stdout/stderr evidence when the executor exposes it.

The coordinator remains responsible for:

- role preparation and stage ordering;
- durable artifact validation and acceptance;
- the single protocol-correction boundary;
- repair counters and resume decisions;
- source identity, commits, PRs, and CI;
- durable role-failure diagnostics.

Runtime diagnostics are bounded and redacted by the existing role-runtime diagnostics layer. Hidden reasoning, authorization data, secrets, and unbounded transcripts are not part of the runtime contract.

## OpenCode runtime

The OpenCode adapter is first-class and remains the default implementation. It owns only OpenCode-specific concerns such as:

- resolving the OpenCode CLI;
- invoking installed `autodev-<role>` agents;
- reading effective OpenCode role/model mappings;
- applying OpenCode-specific privacy authorization before invocation;
- preserving OpenCode-specific argument restrictions such as the prohibition on ad-hoc per-run model overrides.

It returns a common role invocation result to the coordinator. The coordinator does not build or inspect the `opencode run` command.

## Other runtimes

The runtime registry is deliberately separate from AutoDev's model-provider layer. A future runtime can use the existing provider abstractions, a command executor, Codex CLI, or another supported execution mechanism without duplicating provider transports or changing the coordinator state machine.

Adding a production runtime requires registration plus its own configuration/installation contract. Merely naming an unregistered runtime in configuration does not enable it.
