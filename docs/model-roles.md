# Model roles

AutoDev separates deterministic workflow ownership from model execution. The six model-backed **workflow roles** are:

```text
reader
synthesizer
planner
implementer
fixer
verifier
```

OpenCode additionally has an `autodev-coordinator` frontend agent. Its model is part of OpenCode's seven-agent mapping and may be configured independently, but the coordinator agent is not a free-form workflow planner: Python remains authoritative for the next deterministic workflow transition, durable stage state, repair budgets, and shipment decisions.

## OpenCode model routing

For OpenCode runs, model routing is resolved jointly from explicit OpenCode agent mappings, selected AutoDev user model profiles, and OpenCode inheritance. Explicit `opencode.json` / `opencode.jsonc` AutoDev agent models have highest precedence. For example:

```json
{
  "agent": {
    "autodev-coordinator": { "model": "provider/coordinator-model" },
    "autodev-reader": { "model": "provider/reader-model" },
    "autodev-synthesizer": { "model": "provider/synthesizer-model" },
    "autodev-planner": { "model": "provider/planner-model" },
    "autodev-implementer": { "model": "provider/implementer-model" },
    "autodev-fixer": { "model": "provider/fixer-model" },
    "autodev-verifier": { "model": "provider/verifier-model" }
  }
}
```

All OpenCode agent mappings may still be configured independently. AutoDev additionally supports named user-local model profiles in its existing user configuration file. Explicit OpenCode agent models remain highest precedence; a selected AutoDev profile fills workflow roles that would otherwise inherit from the OpenCode coordinator/global/default model. AutoDev passes the resolved profile route through OpenCode's documented `--model provider/model` flag, so scheduled runs do not depend on an interactive `/models` selection.

See [`configuration.md`](configuration.md) for the canonical user-config schema, paths, copyable examples, per-repository selection, and precedence.

Profile selection is machine/user-local. A user-wide active profile may be overridden for one canonical GitHub `OWNER/REPO` in the same user config, so no `.autodev` or `opencode.jsonc` noise file is required in each repository. Root `opencode.json` / `opencode.jsonc` remains supported and can override individual AutoDev roles explicitly.

Example:

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

Inspect the effective safe mapping—including the coordinator—with:

```text
autodev models
```

## Prompt policy

AutoDev applies a role-specific prompt policy to the six workflow roles while preserving explicit issue requirements and output contracts. Current default modes are:

```text
reader       off
synthesizer  lite
planner      lite
implementer  full
fixer        full
verifier     review
```

The policy is an AutoDev-native adaptation: reader minimization is disabled, verifier policy is review-only, and safety/data-integrity requirements always override minimization.

A provider-profile JSON may still carry the current `prompt_policy` and Headroom metadata used when AutoDev prepares role context. It does not replace OpenCode's agent/model mapping.

## Headroom

Headroom is optional. AutoDev's context-optimization layer can use Headroom settings to describe/compress eligible evidence while preserving issue requirements, role policy, patch markers, and verifier output contracts. Direct OpenCode transport remains owned by OpenCode.

See [`headroom.md`](headroom.md) for the current configuration and diagnostic model.

## Privacy

Runtime authorization occurs before model work. AutoDev records safe route/policy metadata but does not persist prompt content or credential values in privacy audit records. Persistent consent grants are explicit and scoped.

See [`privacy.md`](privacy.md).

## Role boundaries

Each of the six workflow roles has a bounded preparation/acceptance contract under `.autodev-run/current`. The runtime must produce an accepted durable artifact before Python advances. A zero process exit without a valid accepted artifact is not success. The OpenCode coordinator frontend delegates those transitions to Python rather than creating a seventh workflow-stage artifact contract.

See [`opencode.md`](opencode.md), [`role-runtimes.md`](role-runtimes.md), and [`python-architecture.md`](python-architecture.md).
