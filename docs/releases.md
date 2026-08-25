# AutoDev releases

Tagged AutoDev releases provide native installers plus reproducible, provenance-attested source bundles for users who want a stable product snapshot. Development can still use a repository checkout, but cloning the repository is not the normal end-user installation path.

## Current release assets

A successful x86-64 release publishes the native installers, source bundles, manifest, and checksums for one exact tag commit:

```text
AutoDev-X.Y.Z-Setup.msi
autodev_X.Y.Z_amd64.deb
autodev-X.Y.Z-1.x86_64.rpm
autodev-vX.Y.Z-common.zip
autodev-vX.Y.Z-windows.zip
autodev-release-manifest.json
SHA256SUMS
```

There is no separate Linux ZIP. The ZIPs remain deterministic source/product snapshots for reproducibility, inspection, advanced/manual workflows, and contributor use; MSI/DEB/RPM are the normal end-user install surfaces.

The MSI, DEB, and RPM share one self-contained AutoDev payload built with a pinned PyInstaller version. The payload includes AutoDev's Python runtime and embeds `autodev-build.json` with the release version and exact 40-character commit SHA. Windows packages install per-user; Linux packages install under `/opt/autodev` with `/usr/bin/autodev` as the canonical launcher.

The common ZIP contains shared Python automation, area reader, OpenCode integration, prompt/agent assets, examples, maintained documentation, and root configuration files needed by all hosts. The Windows ZIP contains Windows-specific source assets.

The source-bundle packager reads file names and bytes from the Git object identified by the release commit rather than from uncommitted working-tree files. ZIP entry order and timestamps are fixed. Native build jobs also set source-date/build reproducibility controls and build each platform output twice; CI requires the unsigned payload/package bytes to match before an artifact can advance.

`autodev-release-manifest.json` schema v2 records the release version, exact 40-character commit SHA, every source bundle's SHA-256 digest and file manifest, plus the MSI/DEB/RPM artifact names, sizes, and SHA-256 digests. `SHA256SUMS` covers the native installers, source archives, and manifest.

## CI and release trust boundary

PR/main CI:

- runs the supported Python Linux/Windows matrix, Bash/PowerShell syntax checks where those surfaces are maintained, canonical CLI smoke tests, and repository hygiene;
- runs `actionlint` across every `.github/workflows/*.yml`/`.yaml` file;
- rejects external `uses:` references that are not pinned to a full 40-character commit SHA;
- builds deterministic source release bundles twice and requires byte-identical output;
- builds the self-contained Windows and Linux payloads twice and requires matching file hashes;
- builds normalized MSI plus DEB/RPM packages twice and requires matching package bytes;
- smoke-tests install, upgrade, failure/recovery, state preservation, PATH/launcher behavior, scheduler non-activation, and uninstall on Windows, Debian/Ubuntu, and a Fedora RPM environment.

The native-package CI gate is part of the version-tag prerequisite. A `main` version tag cannot advance merely because the Python unit suite passed while native installers are broken.

Release publication is **manual and tag-selected**. `.github/workflows/release.yml` accepts an existing canonical `vMAJOR.MINOR.PATCH` tag through `workflow_dispatch`, then:

1. reruns required CI against that exact tag, including native package builds and smoke tests;
2. checks out the tag with full history;
3. requires the ref to be an existing annotated tag;
4. proves the tag commit equals the checked-out `HEAD`;
5. reuses the MSI/DEB/RPM bytes produced by that exact-tag native CI run;
6. builds deterministic source bundles and binds the native hashes into manifest schema v2;
7. validates the complete `SHA256SUMS` set;
8. creates GitHub artifact provenance with `actions/attest` for the checksum subjects;
9. creates the GitHub Release only after all of the above succeeds.

The publish job uses only the repository-provided `GITHUB_TOKEN`; no PAT is required. Its permissions are limited to `contents: write`, `id-token: write`, and `attestations: write`. Ordinary CI remains read-only except for the reusable-workflow permission ceiling needed by the main-only version-tag job.

If the same release workflow is rerun and the GitHub Release already exists, AutoDev downloads the existing assets and requires every expected asset to be byte-identical. It does **not** use `--clobber`. Any missing or mismatched asset fails instead of silently replacing a published artifact from different source bytes.

Generated release notes explicitly start at the most recent **published GitHub Release** tag. Intermediate semantic-version tags that were never published do not truncate the changelog range.

## Install from a release

Verify the complete release set first, then use the package matching the host:

### Windows x86-64

```text
AutoDev-X.Y.Z-Setup.msi
```

### Debian / Ubuntu x86-64

```text
autodev_X.Y.Z_amd64.deb
```

### Fedora / RPM-family x86-64

```text
autodev-X.Y.Z-1.x86_64.rpm
```

See [`installation.md`](installation.md) for package-manager commands, upgrade, failure recovery, uninstall, and state-preservation semantics.

## Verify checksums

After downloading the published assets into one directory, Linux users can run:

```bash
sha256sum -c SHA256SUMS
```

`SHA256SUMS` covers every artifact produced by the release packager. Verification with `-c` therefore expects the complete asset set. If you download only the installer for one host, verify that file directly against its corresponding line instead.

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

Also inspect `autodev-release-manifest.json` and confirm that `version` and `commit_sha` match the tag/commit you intended to install. For native installers, confirm the selected entry under `native_installers` matches the downloaded artifact digest.

## Verify GitHub provenance

With a current GitHub CLI, verify a downloaded artifact against AutoDev's GitHub artifact attestation. For example:

```bash
gh attestation verify ./autodev_X.Y.Z_amd64.deb --repo yaron-E92/AutoDev --signer-workflow yaron-E92/AutoDev/.github/workflows/release.yml
```

The same form can be used for the MSI, RPM, source ZIPs, and manifest. GitHub CLI verifies the artifact digest and Actions identity/provenance rather than trusting the filename alone.

GitHub CLI versions that support release-level verification can additionally run:

```bash
gh release verify vX.Y.Z -R yaron-E92/AutoDev
```

## Windows signing status

The MSI build is **signing-ready but unsigned unless a signing certificate is configured in the release environment**. Reproducibility normalization happens before signing so the unsigned build product has a deterministic source identity. Authenticode signing, when configured, must happen after normalization and before final hashing/manifest/provenance publication; the published checksum then refers to the signed bytes.

Do not infer an Authenticode publisher identity merely from GitHub provenance. GitHub artifact attestation proves the Actions build provenance/digest; Authenticode is a separate Windows publisher-signing layer.

## Package metadata and licensing

Linux packages declare architecture, homepage, and runtime dependencies. The repository currently does not contain a project license file, so package metadata uses `NOASSERTION` rather than inventing a license declaration. A future project license decision should update both source/release documentation and DEB/RPM metadata together.

## Publishing a release

1. Merge reviewed, green changes into `main` through the normal human-controlled merge process.
2. Allow the trusted version policy to create the intended annotated semantic-version tag on green `main`, or otherwise ensure the selected release tag is the exact trusted tag intended by repository policy.
3. Manually run the repository's **Release** workflow and provide that existing tag as its `tag` input.
4. Let exact-tag CI, native installer smoke tests, source-identity checks, deterministic packaging, checksum validation, provenance attestation, and publication complete.
5. Download the created assets, verify `SHA256SUMS`, and verify at least one native installer with `gh attestation verify` before announcing the release.

Do not move or reuse a published tag for different source. If an existing release's assets differ from what the release pipeline produces for the selected tag commit, the workflow intentionally fails rather than replacing them.
