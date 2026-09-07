# Structured-output role contracts

AutoDev owns role-output contracts. A runtime may provide native schema-constrained output, validated native output, emulated structured output, or no structured-output support, but the runtime never owns workflow authority.

The stable capability vocabulary is:

- `native-strict` — the runtime/provider guarantees the supplied schema before returning the value;
- `native-validated` — the runtime exposes a native schema path and validates/retries against the supplied schema before AutoDev receives the value;
- `emulated` — AutoDev must obtain text and deterministically parse/validate it against the same role contract;
- `unsupported` — that runtime cannot provide the role contract natively; the existing text protocol remains available when the role itself has a compatibility parser.

## Authority boundary

A schema-valid value is only a transport/protocol success. AutoDev still materializes the role's established protocol artifact and runs the same deterministic acceptance boundary used by fallback text output. Semantic verifier consistency checks, Planner six-section validation, bounded handoff checks, and later workflow stages remain authoritative.

Native schema retry exhaustion is distinct from a runtime/provider transport failure. Both are also distinct from a value that passed native schema validation but is rejected by AutoDev's post-schema role acceptance. Only the latter uses the ordinary single protocol-correction allowance.

## UX authority

Schemas are static product resources and must not contain issue text, repository content, customer-specific journey/screen/state identifiers, or an AutoDev UX-context fingerprint.

When a role needs to report UX impact, it may return generic references containing a `source_kind` and `source_id`. AutoDev validates those references against the UX context it selected for that role. The effective UX-context fingerprint is AutoDev-owned durable identity; a model cannot assert, replace, or prove that fingerprint in its response.

The native Planner, Synthesizer, and Reader paths persist their structured UX metadata only as a sidecar to the ordinary role artifact. The sidecar is checked at the shared role-acceptance boundary, so an invented UX reference is a protocol rejection rather than a runtime failure.

## Reader control-plane boundary

The structured Reader contract contains only a bounded factual handoff, bounded repository observations, and generic UX references. It deliberately has no execution-classification, attention, queue, manual-boundary, or workflow-stage field.

This preserves the execution-classification v2 architecture: AutoDev classifies work deterministically before Reader, and any legacy Reader classification block remains advisory diagnostics only. Native structured Reader output cannot set or override `ExecutionClassification`, create manual-attention state, or claim an external boundary as control-plane truth. Unsupported extra fields are rejected by the static schema and by AutoDev's defensive materializer without mutating classification state.

## Implementer and Fixer completion evidence

Structured Output does not constrain or replace the Implementer/Fixer repository-editing tool loop. It constrains only the small completion report returned after those edits.

The Implementer v1 completion report contains a single-line commit-message suggestion, a bounded completion summary, and bounded validation notes. AutoDev materializes the same existing `commit-message.txt` artifact and independently computes the source identity before any commit or workflow transition.

The Fixer v1 completion report contains only a bounded summary and validation notes. It does not contain repair success, source identity, verification verdict, workflow-stage completion, commit state, or execution classification. AutoDev independently observes the worktree and reruns the appropriate deterministic or semantic verification stage. A schema-valid completion report is therefore evidence, never self-attested proof that implementation or repair succeeded.

The first completion-report contract intentionally does not ask Implementer/Fixer to self-attest UX conformance. Planner/Verifier provide mechanically validated UX references, and later verification remains authoritative. A future additive contract version may record completion-time UX references if AutoDev can validate them without turning self-reporting into proof.

## OpenCode

For OpenCode, `opencode run --format json` is only a CLI event/output format and is not treated as schema-constrained model output. Native Structured Output uses OpenCode's headless server/session API and sends the AutoDev-owned JSON Schema in the session prompt `format` request. Older OpenCode installations that do not expose that API fall back to the existing CLI/text protocol.

OpenCode's schema retries occur inside the runtime boundary and do not consume AutoDev's one protocol-correction attempt.

## Rollout

Issue #236 migrates roles in authority-sensitive order:

1. Verifier — structured semantic verdict while retaining all existing semantic consistency checks.
2. Planner and Synthesizer — structured planning/handoff values that materialize the existing six-section plan and bounded handoff artifacts.
3. Reader — structured factual evidence without granting the model execution-classification authority.
4. Implementer and Fixer — bounded completion reports without granting commit, workflow-stage, or verification authority.

Contract names and versions are part of durable role snapshot identity. When an effective pinned UX context exists, its AutoDev-owned fingerprint also participates in the role snapshot identity so resume cannot silently reuse an output under a different schema or UX authority context.
