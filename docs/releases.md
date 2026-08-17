# AutoDev releases

AutoDev development can continue to use a repository checkout. Tagged releases provide reproducible, provenance-attested bundles for users who want a stable installable snapshot.

## Release contents

A successful `v*` tag release publishes five assets built from the exact tagged Git commit:

```text
autodev-vX.Y.Z-common.zip
autodev-vX.Y.Z-linux.zip
autodev-vX.Y.Z-windows.zip
autodev-release-manifest.json
SHA256SUMS
```

The common archive contains the shared Python automation, area-reader, OpenCode integration, prompt/agent assets, examples, scripts, and root configuration/docs needed by both platforms. Add the Linux or Windows archive for platform-specific scripts.

The packager reads file names and bytes from the Git object identified by the release commit rather than from uncommitted working-tree files. ZIP entry order and timestamps are fixed, so two package operations for the same commit and version must be byte-identical. CI enforces this with two independent package directories and `diff -ru`.

`autodev-release-manifest.json` records the release version, exact 40-character commit SHA, every bundle's SHA-256 digest, and every source file path/size/SHA-256 digest. `SHA256SUMS` covers all three archives plus the manifest.

## CI and release trust boundary

PR CI:

- runs the existing Python Linux/Windows matrix, Bash/PowerShell syntax checks, smoke tests, and repository hygiene;
- runs `actionlint` across every `.github/workflows/*.yml`/`.yaml` file;
- rejects external `uses:` references that are not pinned to a full 40-character commit SHA;
- builds the deterministic release bundles twice and requires byte-identical output.

Release publication is triggered only by a `v*` tag. The release workflow reuses the same CI workflow, verifies that the semantic-version tag resolves to the workflow's exact `GITHUB_SHA`, packages that SHA, validates `SHA256SUMS`, and creates GitHub artifact provenance using `actions/attest`.

The publish job uses only the repository-provided `GITHUB_TOKEN`; no PAT is required. Its permissions are limited to `contents: write`, `id-token: write`, and `attestations: write`. Ordinary CI remains `contents: read`.

If the same tag workflow is rerun and a GitHub Release already exists, AutoDev downloads the existing release and requires every expected asset to be byte-identical. It does **not** use `--clobber`. Any mismatch fails instead of silently replacing an artifact from different source bytes.

## Install from a release

Download the common archive and the archive for your host platform from the same GitHub Release. Extract them into one AutoDev directory, preserving paths. The shared and platform archives have disjoint release roots, so they can be overlaid safely.

For development from `main`, keep using the normal repository checkout and canonical installer. Release bundles do not change repository-based developer workflows.

## Verify checksums

On Linux/macOS after downloading all release assets into one directory:

```bash
sha256sum -c SHA256SUMS
```

On PowerShell:

```powershell
Get-Content .\SHA256SUMS | ForEach-Object {
    $parts = $_ -split '\s+', 2
    $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $parts[1]).Hash.ToLowerInvariant()
    if ($actual -ne $parts[0].ToLowerInvariant()) {
        throw "Checksum mismatch: $($parts[1])"
    }
}
```

Also inspect `autodev-release-manifest.json` and confirm that `version` and `commit_sha` are the tag/commit you intended to install.

## Verify GitHub provenance

With a current GitHub CLI, verify an individual downloaded archive against AutoDev's artifact attestation:

```bash
gh attestation verify ./autodev-vX.Y.Z-common.zip --repo yaron-E92/AutoDev --signer-workflow yaron-E92/AutoDev/.github/workflows/release.yml
```

Repeat for the Linux/Windows archive you use. GitHub CLI verifies the artifact digest and the signed Actions identity/provenance rather than trusting the filename alone.

GitHub CLI versions that support release-level verification can additionally run:

```bash
gh release verify vX.Y.Z -R yaron-E92/AutoDev
```

## Creating a release

1. Merge only reviewed, green changes into `main` through the normal human-controlled merge process.
2. Create and push a new semantic-version tag such as `v1.2.3` pointing at the intended `main` commit.
3. Let `.github/workflows/release.yml` complete. It cannot publish until the reusable required CI succeeds.
4. Download the created assets, run `sha256sum -c SHA256SUMS`, and verify at least one archive with `gh attestation verify` before announcing the release.

Do not move/reuse a published tag for different source. If an existing release's assets differ from what the deterministic packager produces for the current tag commit, the workflow intentionally fails rather than replacing them.
