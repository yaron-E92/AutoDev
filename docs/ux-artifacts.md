# External UX artifacts

AutoDev can attach an optional, immutable UX specification to a repository without committing a portfolio prototype, screenshots, Figma exports, or another large design corpus to the application repository.

The core contract is transport-neutral. Issue #252 defines the bundle, resolver, cache, locking, and durable-run semantics. Concrete transports plug into that contract separately; OCI/GHCR/ORAS belongs to #253.

## Repository policy

A repository opts in through the existing tracked `.autodev/repo.json`:

```json
{
  "version": 1,
  "ux": {
    "enabled": true,
    "artifact": "example://designs/shuffletask@sha256:0123456789abcdef",
    "product": "shuffletask"
  }
}
```

Repositories with no `ux` field continue unchanged. When UX is enabled, `artifact` and `product` are required. Credentials do not belong in repository policy.

## Bundle contract

A resolved artifact root contains `ux-manifest.json`. Version 1 uses schema `autodev.ux.bundle/v1`:

```json
{
  "schema": "autodev.ux.bundle/v1",
  "product": "shuffletask",
  "contract": "contract.yaml",
  "principles": "principles.md",
  "prototype": "prototype.html",
  "journeys": "journeys.yaml",
  "annexes": ["annexes/mobile.yaml"],
  "references": {"root": "references"},
  "screens": {"inbox": "references/inbox.png"},
  "states": {"empty-inbox": "references/inbox-empty.png"},
  "shared": {"artifact": "example://design-system@sha256:abcdef"},
  "verifier": "verifier.json"
}
```

The manifest is strict JSON so validation is deterministic and dependency-light. Referenced contract/journey/annex files may themselves be YAML or JSON, and prototypes/screenshots remain read-only evidence.

Downstream context generation can select specific screen/state IDs and journeys instead of dumping the entire bundle into every prompt. The interaction contract and shared principles remain baseline context.

## Resolver abstraction

```text
UXArtifactResolver
├── supports(reference)
├── resolve(reference, policy)
├── inspect(reference)
└── identity(reference)
```

`ResolvedUXArtifact` carries immutable identity/reference, a stable local root, the validated manifest, source reference, resolver kind, and safe provenance/cache diagnostics. Resolvers register with `UXResolverRegistry`; coordinator and role code do not branch on providers.

A fake resolver in the test suite exercises this interface without OCI, GHCR, ORAS, HTTP, or another production backend.

## Immutable meaning and durable runs

When a run is prepared, AutoDev records the immutable UX identity and safe evidence in `state.json` and `run-manifest.json`. The issue-selected input fingerprint includes that identity.

Resume re-resolves the configured UX artifact and refuses to continue if its immutable identity changed. It also refuses a durable run that gained or lost enabled UX policy after preparation. A mutable alias may be resolved interactively, but unattended execution requires an immutable reference.

Model-free commands:

```text
autodev ux inspect
autodev ux resolve
autodev ux lock
autodev ux doctor
```

`autodev ux lock` changes only `ux.artifact`, and only after checking that repository policy did not change concurrently.

## Cache

Resolved bundles use a user-level content-addressed cache keyed by immutable identity. Default roots:

- Linux/macOS/XDG: `$XDG_CACHE_HOME/autodev/ux` or `~/.cache/autodev/ux`;
- Windows: `%LOCALAPPDATA%\AutoDev\Cache\ux`;
- override: `$AUTODEV_CACHE_HOME/ux`.

Population uses a per-identity lock plus staging directory. A staged bundle is validated and hashed before atomic promotion. Each entry records identity and deterministic tree digest, so changed/corrupt bytes are detected and repopulated.

Cleanup is explicit with `autodev ux cache-prune --max-entries 20`. Ordinary resolution never mutates the application repository.

## Security boundary

- HTML/JavaScript is never executed during resolution or validation.
- Parent traversal, absolute paths, Windows drive paths, NULs, and resolved paths outside the bundle root are rejected.
- File count, per-file size, and total bundle size are bounded.
- Referenced files must exist before role use.
- Diagnostics redact URL user-info, query, and fragment data.
- UX content cannot override queue policy, privacy grants, CI truth, branch ownership, scheduler claims, or other control-plane state.
- Transport adapters remain responsible for transport authentication, integrity, safe extraction, and secret handling.

## Failure classifications

`unsupported_resolver`, `authentication`, `artifact_not_found`, `mutable_reference_disallowed`, `transport_failure`, `identity_mismatch`, `malformed_bundle`, `unsupported_bundle_schema`, and `unsafe_bundle` are stable resolver-boundary classifications.

If UX is enabled and resolution fails, preparation fails closed before model/implementation work rather than silently falling back to the current UI.

## Adding another transport

A future resolver must implement the common interface, return an immutable identity/reference, use the shared cache safely, validate transport integrity, expose a stable local bundle root, classify failures deterministically, and keep credentials out of repository files/durable diagnostics. It registers with the resolver registry rather than adding provider branches to workflow code.

OCI is the first planned concrete resolver, but the logical bundle and downstream role semantics do not depend on OCI.
