# Structured role output

AutoDev can constrain selected model-role responses with versioned JSON Schema while keeping workflow authority in deterministic AutoDev code.

## Contract model

Structured output is an AutoDev protocol capability, not a model/provider authority mechanism.

A role contract has a stable product-owned identity and version, a static JSON Schema, a semantic validator, and a compatibility fallback parser. Schemas live under `automation/schemas/` and must not dynamically embed repository source, issue text, credentials, private filenames, UX artifact contents, or customer-specific journey/screen/state IDs.

The initial production contract is `autodev.semantic-verifier/v1`.

Runtime capability is expressed through provider-neutral values:

- `native-strict`
- `native-validated`
- `emulated`
- `unsupported`

Roles do not branch on provider names to decide whether Structured Output exists. The selected role runtime owns that capability decision from the same effective role/model mapping used for execution and privacy checks.

## OpenCode native path

`opencode run --format json` formats CLI events; it does not constrain the model response to an AutoDev JSON Schema.

For a role with a structured contract, the OpenCode runtime instead attempts the documented headless server/session path:

1. start a loopback-only `opencode serve` instance for the invocation;
2. create an isolated session;
3. send the role prompt through the session message endpoint with the selected AutoDev agent/model and a JSON-schema `format` object;
4. use OpenCode's validated `info.structured_output` value;
5. delete the session and stop the temporary server.

The request uses the effective `provider/model` selected by the existing model-profile resolution introduced by #235. It does not silently fall back to some unrelated OpenCode default model.

OpenCode schema retries are runtime-internal. They do **not** consume AutoDev's one protocol-correction attempt.

### Compatibility fallback

Native Structured Output is additive. If an otherwise supported installed OpenCode version does not expose the required server/format capability, AutoDev retains the existing CLI/text/artifact path and existing post-hoc parser/semantic validation.

Lack of native support by itself therefore does not make a previously supported runtime unusable.

A genuine native schema-exhaustion result is different: after OpenCode has accepted the JSON-schema request and exhausted its schema retries, AutoDev reports structured-output exhaustion rather than hiding the failure by starting a second free-form model attempt.

## Schema validity is not semantic authority

A JSON Schema proves shape only.

After native decoding, the semantic verifier goes through the same AutoDev-owned semantic checks used by the fallback parser. In particular:

- `pass` cannot coexist with blocking findings or unmet acceptance criteria;
- omitted acceptance criteria are still rejected;
- `repair` still requires a repair brief;
- a schema-valid model statement cannot override deterministic execution classification or another AutoDev control-plane decision;
- a schema-valid UX finding cannot make a false UX-conformance claim authoritative.

Native schema success therefore does not bypass the normal verifier acceptance/correction boundary.

## UX authority

Pinned UX context remains AutoDev-owned state from #262/#263.

The verifier schema contains only generic fields such as:

```json
{
  "source_kind": "state",
  "source_id": "task-editor-empty",
  "status": "violated",
  "evidence": "bounded evidence",
  "required_change": "repair guidance"
}
```

`source_kind` is protocol-constrained, but `source_id` is ordinary model output. AutoDev checks every returned reference against the already-selected effective UX context. Customer-specific identifiers are never compiled into the schema.

The model never owns or proves the `ux_context_fingerprint`. AutoDev computes the fingerprint, passes the same UX prompt/fingerprint through initial and protocol-correction invocations, and binds durable role snapshot identity to the contract/schema and effective UX-context fingerprint. A changed effective context therefore invalidates the affected durable role identity rather than trusting a model echo.

## Diagnostics and privacy

Role-attempt diagnostics may record safe protocol metadata:

- role contract name/version;
- structured-output mode and final state;
- schema retry count;
- whether UX context was active;
- the AutoDev-owned UX-context fingerprint;
- runtime/model identity already allowed by the existing safe diagnostics contract.

They do not persist the native structured payload as diagnostic metadata. The payload is materialized only into the role's existing workflow artifact (for the verifier, `verification-result.json`) and then handled by the normal workflow/privacy lifecycle.

## Failure classification

AutoDev keeps distinct observable boundaries for:

- runtime/provider transport failure;
- native schema exhaustion;
- unsupported native capability using compatibility fallback;
- post-schema semantic/protocol inconsistency.

This distinction is important for retry policy: serialization retries belong to the native runtime, semantic repair remains AutoDev-owned, and transient provider/runtime failures must not be mistaken for malformed role protocol.

## Rollout

Migration is intentionally incremental:

1. semantic verifier first;
2. planner and synthesizer bounded artifacts;
3. Reader factual/advisory handoff, preserving deterministic control-plane authority;
4. implementer/fixer completion reports where constraining the report reduces protocol noise without constraining repository editing.

Legacy parsing/correction machinery remains available while equivalent fallback semantics are required.
