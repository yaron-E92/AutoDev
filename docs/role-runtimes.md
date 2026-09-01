# AutoDev role runtimes

AutoDev's Python coordinator owns ordering, durable state, verification, repair budgets, shipment, and resume. Model-heavy work executes through a role-runtime boundary.

```text
Python coordinator/state machine
        -> role runtime
        -> role/model execution
```

The runtime cannot decide which workflow stage runs next and cannot make artifact acceptance authoritative.

## Default runtime

`opencode` is the default runtime. It launches the installed AutoDev role agents and resolves effective workflow-role models from explicit OpenCode agent mappings, selected AutoDev user model profiles, and existing OpenCode inheritance in that precedence order.

See [`configuration.md`](configuration.md) for the user-config schema and model-profile precedence.

Inspect the effective OpenCode role/model mapping with:

```text
autodev models
```

## Selecting a runtime

Precedence is:

1. explicit `--runtime`;
2. `AUTODEV_ROLE_RUNTIME`;
3. repository `.autodev/config.json` `role_runtime`;
4. user AutoDev configuration `role_runtime`;
5. `opencode`.

Example:

```text
autodev issue-to-pr 123 --runtime opencode
```

`autodev coordinate --arguments 123 --runtime opencode` is the equivalent advanced/integration spelling.

An unknown explicitly selected runtime fails before model work. AutoDev does not silently fall back to another runtime.

## Runtime identity and resume

The selected runtime contributes to safe execution fingerprints. Changing the runtime for already-completed role work follows the same invalidation rules as changing other execution-affecting role configuration. A rejected switch does not overwrite the accepted manifest identity.

## Runtime contract

A runtime supplies safe execution identity, bounded privacy evidence for its effective role routes, and a bounded invocation result. Runtime/provider adapters may inspect or shape their own transport configuration, but AutoDev's common privacy layer owns persistent grant matching and final policy authorization. Python remains responsible for preparation, output validation, protocol correction, repair budgets, source identity, commits, PR/CI, and durable failure diagnostics.

Adding another production runtime requires an explicit registry/installation/configuration contract. Naming an unregistered runtime does not enable it.
