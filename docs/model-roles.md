# Model roles

AutoDev separates deterministic workflow ownership from model execution. The model-backed roles are:

```text
reader
synthesizer
planner
implementer
fixer
verifier
```

The coordinator is not a free-form model planner; Python selects the next deterministic workflow transition.

## OpenCode model routing

For OpenCode runs, OpenCode configuration is authoritative. Configure role models through `opencode.json` or `opencode.jsonc`, for example:

```json
{
  "agent": {
    "autodev-reader": { "model": "provider/reader-model" },
    "autodev-synthesizer": { "model": "provider/synthesizer-model" },
    "autodev-planner": { "model": "provider/planner-model" },
    "autodev-implementer": { "model": "provider/implementer-model" },
    "autodev-fixer": { "model": "provider/fixer-model" },
    "autodev-verifier": { "model": "provider/verifier-model" }
  }
}
```

Roles may be mapped independently. Unspecified roles use normal OpenCode inheritance. AutoDev does not duplicate this mapping in `.autodev` and does not support ad-hoc per-run model override flags that would create a competing routing layer.

Inspect the effective safe role/model mapping with:

```text
autodev models
```

## Prompt policy

AutoDev applies a role-specific prompt policy derived from Ponytail principles while preserving explicit issue requirements and output contracts. Current default modes are:

```text
reader       off
synthesizer  lite
planner      lite
implementer  full
fixer        full
verifier     review
```

The policy is an AutoDev-native adaptation: reader minimization is disabled, verifier policy is review-only, and safety/data-integrity requirements always override minimization.

A provider-profile JSON may still carry the current `prompt_policy` and Headroom metadata used when AutoDev prepares role context. It does not replace OpenCode's role/model mapping.

## Headroom

Headroom is optional. AutoDev's context-optimization layer can use Headroom settings to describe/compress eligible evidence while preserving issue requirements, role policy, patch markers, and verifier output contracts. Direct OpenCode transport remains owned by OpenCode.

See [`headroom.md`](headroom.md) for the current configuration and diagnostic model.

## Privacy

Runtime authorization occurs before model work. AutoDev records safe route/policy metadata but does not persist prompt content or credential values in privacy audit records. Persistent consent grants are explicit and scoped.

See [`privacy.md`](privacy.md).

## Role boundaries

Each role has a bounded preparation/acceptance contract under `.autodev-run/current`. The runtime must produce an accepted durable artifact before Python advances. A zero process exit without a valid accepted artifact is not success.

See [`opencode.md`](opencode.md), [`role-runtimes.md`](role-runtimes.md), and [`python-architecture.md`](python-architecture.md).
