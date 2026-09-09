# Multimodal verifier capability discovery

AutoDev only sends pinned UX reference images and deterministic implementation captures to a verifier after the effective verifier route has authoritative image-input capability evidence.

## Capability states

The runtime-neutral capability contract records one of:

- `supported` — the effective runtime/provider/model route is explicitly known to accept image input;
- `unsupported` — authoritative runtime metadata says the route does not accept image input;
- `unknown` — AutoDev cannot establish the route's image-input capability authoritatively.

`unsupported` and `unknown` both fail closed for visual UX verification. Textual and structured UX verification remain independent of this visual capability gate.

## OpenCode

For OpenCode, AutoDev first resolves the actual verifier `provider/model` route. Capability evidence then comes from one of two runtime-owned sources:

1. explicit `modalities.input` on the resolved model configuration; or
2. the resolved model object emitted by `opencode models <provider> --verbose`, specifically `capabilities.input.image`.

AutoDev does not maintain model-name allowlists and does not infer vision support merely because OpenCode's transport accepts image file parts.

An explicitly configured custom model that omits `modalities` is treated as `unknown` rather than trusting fallback capability assumptions. Declare accurate modalities for custom models when they are known, for example:

```json
{
  "provider": {
    "my-provider": {
      "models": {
        "my-vision-model": {
          "modalities": {
            "input": ["text", "image"],
            "output": ["text"]
          }
        }
      }
    }
  }
}
```

## Durable evidence

During an active run, AutoDev writes safe capability evidence to:

```text
.autodev-run/current/ux-multimodal-capability.json
```

The record contains the capability state, runtime, provider, model, exact route, evidence source, bounded diagnostic detail, a hash of the authoritative metadata used, and an evidence fingerprint. It contains no credentials or image bytes.

Capability evidence is bound to the effective verifier route. Before any image submission, AutoDev resolves the verifier route again and rejects the invocation if it differs from the route that was capability-checked. This prevents stale capability evidence from authorizing a different model.

## Other runtimes

A role runtime can expose the same provider-neutral contract through an `image_input_capability(...)` hook returning `ImageInputCapability`. Runtime-specific discovery remains behind the capability seam; multimodal UX verification only consumes the normalized evidence and does not need provider-specific heuristics.
