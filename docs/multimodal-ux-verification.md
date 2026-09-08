# Multimodal UX conformance verification

AutoDev treats **UX presence** and **UX conformance** as different guarantees.

Pinned UX context proves that the selected contract, principles, journeys, screens, states, and references were supplied to the relevant roles. It does not prove that the implementation actually renders the approved hierarchy or state. For UX-enabled work with selected visual references, the semantic stage therefore performs a separate multimodal conformance check before a semantic `pass` can proceed to PR/CI.

## What the verifier compares

AutoDev starts from the already-persisted `ux-context-verifier.json`. It resolves the same immutable pinned UX artifact and selects only image paths belonging to the screen/state/journey IDs in that effective verifier context. Other images in the bundle are not attached simply because they exist.

Supported reference/capture formats are PNG, JPEG, GIF, and WebP. Each image is bounded to 20 MiB. AutoDev hashes the reference bytes and the implementation capture bytes and gives the multimodal verifier stable logical target IDs such as `screen:task-editor` or `state:task-editor-empty`.

The verifier returns a versioned AutoDev-owned structured contract. Each comparison and UX finding is validated against AutoDev-owned target IDs, source IDs, and hashes. Model-generated prose cannot change the pinned UX artifact, select unrelated UX authority, or manufacture a pass for a missing target.

A visual result is one of:

- `pass`: every selected visual target was compared and satisfied;
- `repair`: at least one target was violated and the model returned a bounded repair brief;
- `unverifiable`: required capture/image/model evidence was unavailable or could not be trusted;
- `not-applicable`: the effective verifier UX context contains no selected visual reference.

`unverifiable` is never treated as a visual-conformance pass.

## Repository capture contract

AutoDev does not scrape a developer desktop or invent application launch/navigation logic. Repositories provide deterministic implementation screenshots through `.autodev/ux-capture.json`.

Example:

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

The command is executed directly as an argv array; AutoDev does not pass it through a shell. For each target AutoDev supplies:

```text
AUTODEV_UX_CAPTURE_TARGET
AUTODEV_UX_CAPTURE_SOURCE_KIND
AUTODEV_UX_CAPTURE_SOURCE_ID
AUTODEV_UX_CAPTURE_OUTPUT
AUTODEV_UX_CAPTURE_VIEWPORT
AUTODEV_UX_CAPTURE_PLATFORM
```

The capture program must write exactly the requested image to `AUTODEV_UX_CAPTURE_OUTPUT`. AutoDev places that path under `.autodev-run/current/ux-captures/`, validates the file type and size, and hashes the resulting bytes.

The command provider is intentionally repository/runtime-neutral. A web project may implement it with its existing Playwright/browser test infrastructure; a desktop project may use its established test harness; another repository may use a deterministic fixture renderer. First-party AutoDev capture adapters are tracked separately rather than coupling the verifier contract to one UI stack.

## Multimodal runtime and privacy

The multimodal layer has its own provider-neutral runtime capability seam. A runtime must explicitly support image attachments before reference/capture images are submitted. If image input is unavailable or the runtime rejects the multimodal request, AutoDev records `unverifiable` rather than silently falling back to text and claiming visual success.

The OpenCode adapter uses the same effective verifier provider/model route as semantic verification. Before any screenshot/reference bytes are encoded or submitted, AutoDev performs the existing verifier privacy evaluation and authorization. Image evidence is customer/repository content and receives the same privacy treatment as other model input.

The current OpenCode transport can send image file parts, but AutoDev does not yet have authoritative per-model image-modality metadata from OpenCode. Consequently, transport-level capability is explicit while a model that rejects images fails closed as `unverifiable`. Model-specific modality discovery is a tracked follow-up.

## Repair flow

A semantic verifier `pass` is not sufficient when selected visual authority exists. AutoDev captures the implementation and runs multimodal verification before the semantic stage can continue.

If the multimodal verifier returns `repair`, AutoDev materializes a bounded `verification-repair.md` from validated UX findings and sends it through the existing semantic Fixer budget. The Fixer still receives the same pinned UX authority. After repair, deterministic checks and the verifier run again; the UI is recaptured and compared again. There is no unbounded screenshot/fix loop.

If required visual verification is `unverifiable`, the semantic stage blocks with the multimodal evidence artifact instead of opening a PR under a false full-conformance claim.

## Durable evidence and resume

`.autodev-run/current/ux-multimodal-verification.json` records safe audit evidence including:

- immutable UX artifact identity and effective UX-context fingerprint;
- selected reference logical IDs, safe bundle-relative paths, MIME types, sizes, and SHA-256 hashes;
- implementation capture logical IDs, viewport/platform metadata, sizes, and SHA-256 hashes;
- capture-config hash and aggregate capture identity;
- multimodal verifier contract identity/version;
- effective runtime/model and image-capability status;
- validated comparison statuses and bounded UX findings.

The verifier UX-context fingerprint also includes the repository capture-config hash. Selected UX reference bytes are already part of the effective UX-context fingerprint. Changing either therefore invalidates the verifier role identity on resume. Changed implementation/source state is still governed by the existing deterministic source-proof and semantic resume boundaries, while each fresh visual verification records the resulting capture hash.

## Security boundaries

Normal multimodal verification never executes pinned prototype HTML or JavaScript. Static selected image references are preferred. Prototype rendering, if introduced later, requires a separate explicit sandbox with network/filesystem/script/origin controls.

The capture seam must target only the repository/application under test. It must not capture arbitrary desktops, unrelated windows, notifications, credential dialogs, or ambient user content. Repositories should avoid credential-bearing UI states in capture fixtures.

## Current scope and follow-ups

This implementation establishes the production evidence/capability/repair contract and a deterministic command capture provider. It deliberately does not pretend to solve every platform. Follow-up issues track first-party browser capture, first-party desktop/mobile capture, richer journey/state replay, and authoritative model-modality discovery.
