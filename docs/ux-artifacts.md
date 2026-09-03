# External UX artifacts

AutoDev can attach an optional, immutable UX specification to a repository without committing a portfolio prototype, screenshots, Figma exports, or another large design corpus to the application repository.

The core contract is transport-neutral. Issue #252 defines the bundle, resolver, cache, locking, and durable-run semantics. Concrete transports plug into that contract separately. OCI is the first production transport; GHCR is the first documented registry and ORAS is the initial client.

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

## OCI transport

OCI UX references use an explicit `oci://` scheme:

```text
oci://ghcr.io/<owner>/ux/<product>:<human-tag>
oci://ghcr.io/<owner>/ux/<product>@sha256:<manifest-digest>
```

The resolver is registry-neutral. `ghcr.io` is only a recommended first registry; Harbor, Zot, Artifactory OCI, or another standards-compatible registry can use the same reference and resolver semantics.

Recommended GHCR naming:

```text
ghcr.io/yaron-e92/ux/portfolio:2026-09-03
ghcr.io/yaron-e92/ux/shuffletask:v1
ghcr.io/yaron-e92/ux/shuffletask@sha256:...
```

Human-readable tags are publication/discovery aliases. The immutable OCI manifest digest is the durable identity. Interactive resolution may resolve a tag, but unattended scheduler/headless execution rejects a mutable tag and requires an explicit `autodev ux lock` first.

### ORAS prerequisite

AutoDev currently uses the ORAS CLI as an external OCI client. Native AutoDev installers do not silently install or update ORAS.

Supported ORAS versions start at **1.3.0**. The current documented ORAS release is 1.3.2. Verify the installed tool with:

```text
oras version
autodev ux doctor
```

Follow the official ORAS installation instructions for Windows or Linux. For example, ORAS publishes release archives for both platforms and can also be installed through its supported package-manager paths. AutoDev checks the executable, minimum version, and the required `resolve`, `manifest fetch`, `blob fetch`, and `push` capabilities before use.

### Authentication

Do not put registry tokens in `.autodev/repo.json`.

AutoDev accepts these credential paths, in precedence order:

1. `AUTODEV_OCI_USERNAME` + `AUTODEV_OCI_PASSWORD`;
2. `AUTODEV_OCI_TOKEN` (with a username for GHCR, or identity-token mode for registries that support it);
3. `GITHUB_TOKEN` + `GITHUB_ACTOR` for `ghcr.io` in GitHub Actions;
4. existing ORAS-compatible local registry credential-store/login state.

Secrets are passed to ORAS through stdin, not process arguments, and the token/password environment variables are removed from the ORAS subprocess environment.

For a local GHCR login, a read-only worker credential is preferred:

```text
printf '%s\n' "$GHCR_TOKEN" | oras login ghcr.io -u "$GHCR_USER" --password-stdin
```

For scheduler workers, use the minimum package permission required to pull the UX artifact. A package-read credential does not become GitHub repository authority inside AutoDev.

Public GHCR packages can normally resolve without credentials, but they are still subject to digest, OCI artifact-type, archive-safety, and UX bundle-schema validation.

### Publication

AutoDev provides a narrow model-free publisher for UX bundles:

```text
autodev ux publish ./ux-bundle \
  --to oci://ghcr.io/yaron-e92/ux/shuffletask:v1
```

Publication first validates the local `ux-manifest.json`, then creates one deterministic gzip-compressed tar layer with media type:

```text
application/vnd.autodev.ux.bundle.v1.tar+gzip
```

The OCI manifest uses AutoDev artifact type:

```text
application/vnd.autodev.ux.bundle.v1
```

The command prints the immutable manifest digest/reference returned by ORAS. It does **not** rewrite application repository policy. If the published artifact should become repository authority, update or configure the tagged reference explicitly and then run:

```text
autodev ux lock
```

### Resolution and inspection

```text
autodev ux inspect oci://ghcr.io/yaron-e92/ux/shuffletask@sha256:...
autodev ux resolve oci://ghcr.io/yaron-e92/ux/shuffletask@sha256:...
autodev ux doctor
```

OCI resolution verifies:

1. OCI reference syntax;
2. immutable manifest digest;
3. expected AutoDev OCI artifact type;
4. exactly one expected UX bundle layer;
5. layer size and digest;
6. safe tar extraction without symlinks, devices, absolute paths, or traversal;
7. the v1 UX bundle manifest and content limits;
8. the immutable cache entry.

A valid cached immutable OCI artifact is reused without re-downloading the manifest or bundle bytes.

For local development/CI only, `AUTODEV_OCI_PLAIN_HTTP=1` enables plain HTTP **only for loopback registries** (`localhost`, `127.0.0.1`, or `[::1]`). It cannot disable TLS for remote registries.

### OCI failure classes

OCI/ORAS adds two actionable tool classifications to the core resolver failures:

- `missing_tool`: ORAS is not available;
- `unsupported_tool_version`: ORAS is too old or lacks required capabilities.

Registry authentication, not-found, transport, digest mismatch, malformed artifact type/bundle, unsupported UX schema, and unsafe archive failures continue to use the provider-neutral classifications documented above.

The logical UX bundle and downstream role semantics remain independent from OCI. A future resolver plugs into the same registry without changing issue semantics, prompt selection, durable-run identity, or repository UX policy.
