# Multimodal UX conformance verification

AutoDev treats **UX presence** and **UX conformance** as different guarantees.

Pinned UX context proves that the selected contract, principles, journeys, screens, states, and references were supplied to the relevant roles. It does not prove that the implementation actually renders the approved hierarchy or state. For UX-enabled work with selected visual references, the semantic stage therefore performs a separate multimodal conformance check before a semantic `pass` can proceed to PR/CI.

## What the verifier compares

AutoDev starts from the already-persisted `ux-context-verifier.json`. It resolves the same immutable pinned UX artifact and selects only image paths belonging to the screen/state/journey IDs in that effective verifier context. Other images in the bundle are not attached simply because they exist.

A selected journey or state whose own artifact entry is not an image may explicitly map to one pinned image through its `.autodev/ux-capture.json` target `reference`. For example, `journey:checkout-complete` may compare its reproduced browser state against `screen:confirmation`. The selected journey remains the comparison/source identity; the durable evidence records the separate `reference_target_id`. Missing, non-image, or unpinned indirect references fail closed rather than causing AutoDev to search the whole UX bundle.

Supported reference/capture formats are PNG, JPEG, GIF, and WebP. Each image is bounded to 20 MiB. AutoDev hashes the reference bytes and the implementation capture bytes and gives the multimodal verifier stable logical target IDs such as `screen:task-editor` or `state:task-editor-empty`.

The verifier returns a versioned AutoDev-owned structured contract. Each comparison and UX finding is validated against AutoDev-owned target IDs, source IDs, and hashes. Model-generated prose cannot change the pinned UX artifact, select unrelated UX authority, or manufacture a pass for a missing target.

A visual result is one of:

- `pass`: every selected visual target was compared and satisfied;
- `repair`: at least one target was violated and the model returned a bounded repair brief;
- `unverifiable`: required capture/image/model evidence was unavailable or could not be trusted;
- `not-applicable`: the effective verifier UX context contains no selected visual reference.

`unverifiable` is never treated as a visual-conformance pass.

## Repository capture contract

AutoDev does not scrape a developer desktop or infer application launch/navigation logic from model prose. Repositories declare deterministic implementation capture through `.autodev/ux-capture.json`.

Two providers use the same bounded target/output/evidence contract:

- `command`: invokes a repository-owned argv capture command, retained for runtime/platform-neutral integration;
- `browser`: AutoDev's first-party Chrome/Chromium/Edge provider with explicit origin, route, viewport, readiness, and bounded state/journey replay.

Command-provider example:

```json
{
  "schema": "autodev.ux.capture/v1",
  "provider": "command",
  "command": ["node", "scripts/capture-ux.mjs"],
  "timeout_seconds": 120,
  "targets": {
    "state:task-editor-empty": {
      "source_kind": "state",
      "source_id": "task-editor-empty",
      "output": "task-editor-empty.png",
      "viewport": "1280x720",
      "platform": "web"
    }
  }
}
```

The command is executed directly as an argv array; AutoDev does not pass it through a shell. For each command target AutoDev supplies:

```text
AUTODEV_UX_CAPTURE_TARGET
AUTODEV_UX_CAPTURE_SOURCE_KIND
AUTODEV_UX_CAPTURE_SOURCE_ID
AUTODEV_UX_CAPTURE_OUTPUT
AUTODEV_UX_CAPTURE_VIEWPORT
AUTODEV_UX_CAPTURE_PLATFORM
```

The capture program must write exactly the requested image to `AUTODEV_UX_CAPTURE_OUTPUT`. AutoDev places that path under `.autodev-run/current/ux-captures/`, validates the file type and size, and hashes the resulting bytes.

The first-party browser provider uses the same `CaptureTarget`/`CapturedImage` seam. It opens an isolated headless browser profile and a fresh DevTools page; it does not attach to ambient user tabs or windows. Navigation and HTTP(S) requests are restricted to explicit `browser.allowed_origins`. Repository configuration may declare only bounded `click`, `fill`, `select`, and `wait` actions—there is no arbitrary JavaScript action. See [`browser-ux-capture.md`](browser-ux-capture.md) for the full configuration and trust boundary.

## Multimodal runtime and privacy

The multimodal layer has its own provider-neutral runtime capability seam. A runtime must authoritatively report image-input support for the effective verifier route before reference/capture images are submitted. Capability evidence is normalized to supported/unsupported/unknown, bound to runtime/provider/model identity, and persisted for audit.

The OpenCode adapter uses the same effective verifier provider/model route as semantic verification. It prefers explicit resolved model modalities and otherwise reads OpenCode's resolved verbose model metadata for `capabilities.input.image`. AutoDev does not infer vision support merely because a transport accepts file parts, and it rechecks route identity before image submission so stale capability evidence cannot authorize a changed model route.

Before any screenshot/reference bytes are encoded or submitted, AutoDev performs the existing verifier privacy evaluation and authorization. Image evidence is customer/repository content and receives the same privacy treatment as other model input. Unsupported or unknown image capability fails closed as `unverifiable` while textual/structured UX verification remains independent.

See [`multimodal-capability-discovery.md`](multimodal-capability-discovery.md) for capability-source and route-binding details.

## Repair flow

A semantic verifier `pass` is not sufficient when selected visual authority exists. AutoDev captures the implementation and runs multimodal verification before the semantic stage can continue.

If the multimodal verifier returns `repair`, AutoDev materializes a bounded `verification-repair.md` from validated UX findings and sends it through the existing semantic Fixer budget. The Fixer still receives the same pinned UX authority. After repair, deterministic checks and the verifier run again; the UI is recaptured and compared again. There is no unbounded screenshot/fix loop.

If required visual verification is `unverifiable`, the semantic stage blocks with the multimodal evidence artifact instead of opening a PR under a false full-conformance claim.

## Durable evidence and resume

`.autodev-run/current/ux-multimodal-verification.json` records safe audit evidence including:

- immutable UX artifact identity and effective UX-context fingerprint;
- selected reference logical IDs, any indirect `reference_target_id`, safe bundle-relative paths, MIME types, sizes, and SHA-256 hashes;
- implementation capture logical IDs, viewport/platform metadata, sizes, and SHA-256 hashes;
- capture-config hash and aggregate capture identity;
- multimodal verifier contract identity/version;
- effective runtime/model and authoritative image-capability status;
- validated comparison statuses and bounded UX findings.

AutoDev also computes a safe multimodal **input identity** and **evidence identity** for operator inspection. The input identity covers the pinned UX/context, capture configuration, selected reference evidence, implementation capture evidence, verifier contract, and effective runtime metadata. The evidence identity additionally binds the final comparison/findings result.

Before a completed semantic checkpoint is reused, AutoDev deterministically revalidates the non-model visual evidence against the current run. It checks:

- the effective UX-context fingerprint and pinned artifact identity;
- the current verifier-output contract metadata;
- the selected reference target set, paths, mappings, and bytes;
- `.autodev/ux-capture.json` identity when capture is relevant;
- configured target output, viewport, and platform metadata;
- the actual bytes, MIME type, size, and aggregate identity of each persisted implementation capture.

If any of those inputs changed, resume is refused with an explicit `--invalidate-role verifier` requirement. That existing invalidation mechanism clears the completed semantic checkpoint and any downstream `pr-created` checkpoint, forcing verification/capture/CI to be re-established before the run can become ready again. A verifier runtime/model configuration change continues to use the existing role-snapshot invalidation rule, so execution-affecting route changes likewise cannot reuse old semantic work.

A `not-applicable` visual result does not become stale merely because a repository has an otherwise-unused capture configuration; only semantically relevant visual inputs participate in that result's resume check.

`autodev resume ... status` includes concise multimodal status, checkpoint state, compared targets, evidence identity, violation/unverifiable counts, and route capability when evidence exists. `autodev tui` exposes the same AutoDev-owned inspection data, and `autodev tui --once --json` provides it as structured JSON. Screenshot bytes themselves are not copied into generic status/TUI output.

## Security boundaries

Normal multimodal verification never executes pinned prototype HTML or JavaScript. Static selected image references are preferred. The first-party browser capture provider runs only the repository application under test; a pinned UX prototype is never substituted as executable application content.

Capture targets must not include arbitrary desktops, unrelated windows, notifications, credential dialogs, or ambient user content. The browser provider uses an isolated temporary profile, a fresh DevTools page, explicit application origins, and request interception. Repositories should still avoid credential-bearing UI states in capture fixtures.

## Current scope and follow-ups

AutoDev now has the production evidence/capability/repair contract, the runtime-neutral command capture provider, authoritative verifier image-capability discovery, and a first-party browser provider with deterministic screen/state/journey replay. First-party desktop/mobile capture and broader platform-specific evidence acquisition remain separate follow-ups rather than weakening the existing trust boundary.
