# AutoDev releases

Tagged AutoDev releases provide reproducible, provenance-attested bundles for users who want a stable product snapshot. Development can still use a repository checkout, but cloning the repository is not the normal end-user installation path.

Native Windows MSI and Linux DEB/RPM packages are **not available yet**. They are tracked separately by #184 and #185 and must not be documented as existing release artifacts before that shared installer work lands.

## Current release assets

A successful release currently publishes four deterministic assets built from the exact selected tag commit:

```text
autodev-vX.Y.Z-common.zip
autodev-vX.Y.Z-windows.zip
autodev-release-manifest.json
SHA256SUMS
```

There is no separate Linux ZIP in the current packager. Linux/other POSIX users use the common archive. Windows users overlay the common and Windows archives from the same release.

The common archive contains the shared Python automation, area reader, OpenCode integration, prompt/agent assets, examples, maintained documentation, and root configuration files needed by all hosts. The Windows archive contains Windows-specific assets.

The packager reads file names and bytes from the Git object identified by the release commit rather than from uncommitted working-tree files. ZIP entry order and timestamps are fixed, so two package operations for the same commit and version must be byte-identical. CI enforces this with two independent package directories and `diff -ru`.

`autodev-release-manifest.json` records the release version, exact 40-character commit SHA, every bundle's SHA-256 digest, and every source file path/size/SHA-256 digest. `SHA256SUMS` covers both archives plus the manifest.

## CI and release trust boundary

PR CI:

- runs the supported Python Linux/Windows matrix, Bash/PowerShell syntax checks where those surfaces are maintained, canonical CLI smoke tests, and repository hygiene;
- runs `actionlint` across every `.github/workflows/*.yml`/`.yaml` file;
- rejects external `uses:` references that are not pinned to a full 40-character commit SHA;
- builds the deterministic release bundles twice and requires byte-identical output.

Release publication is **manual and tag-selected**. `.github/workflows/release.yml` accepts an existing canonical `vMAJOR.MINOR.PATCH` tag through `workflow_dispatch`, then:

1. reruns required CI against that exact tag;
2. checks out the tag with full history;
3. requires the ref to be an existing annotated tag;
4. proves the tag commit equals the checked-out `HEAD`;
5. packages that exact commit;
6. validates `SHA256SUMS`;
7. creates GitHub artifact provenance with `actions/attest`;
8. creates the GitHub Release only after all of the above succeeds.

The publish job uses only the repository-provided `GITHUB_TOKEN`; no PAT is required. Its permissions are limited to `contents: write`, `id-token: write`, and `attestations: write`. Ordinary CI remains `contents: read`.

If the same release workflow is rerun and the GitHub Release already exists, AutoDev downloads the existing assets and requires every expected asset to be byte-identical. It does **not** use `--clobber`. Any missing or mismatched asset fails instead of silently replacing a published artifact from different source bytes.

## Install from a release

Download all required files from one release and keep versions together:

### Linux / other POSIX

```text
autodev-vX.Y.Z-common.zip
autodev-release-manifest.json
SHA256SUMS
```

### Windows

```text
autodev-vX.Y.Z-common.zip
autodev-vX.Y.Z-windows.zip
autodev-release-manifest.json
SHA256SUMS
```

Verify the artifacts first. Then extract them into one permanent AutoDev directory. On Windows, overlay the two ZIPs while preserving paths.

Until #184/#185 replace this bootstrap with native installers, install the public launcher from the extracted release directory with:

```text
python -m automation.autodev_cli install --user --add-to-path
```

After opening a new shell, normal end-user workflows use `autodev`; source-module invocation is not the normal operational interface. See [`installation.md`](installation.md).

## Verify checksums

On Linux/macOS after downloading the assets you intend to use into one directory:

```bash
sha256sum -c SHA256SUMS
```

`SHA256SUMS` covers every release artifact produced by the current packager. Therefore checksum verification expects the complete published asset set; if you downloaded only the common archive, either download the remaining published assets before using `-c` or verify the common archive directly against its line in `SHA256SUMS`.

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

Also inspect `autodev-release-manifest.json` and confirm that `version` and `commit_sha` match the tag/commit you intended to install.

## Verify GitHub provenance

With a current GitHub CLI, verify an individual downloaded archive against AutoDev's artifact attestation:

```bash
gh attestation verify ./autodev-vX.Y.Z-common.zip --repo yaron-E92/AutoDev --signer-workflow yaron-E92/AutoDev/.github/workflows/release.yml
```

Repeat for the Windows archive when applicable. GitHub CLI verifies the artifact digest and the signed Actions identity/provenance rather than trusting the filename alone.

GitHub CLI versions that support release-level verification can additionally run:

```bash
gh release verify vX.Y.Z -R yaron-E92/AutoDev
```

## Publishing a release

1. Merge reviewed, green changes into `main` through the normal human-controlled merge process.
2. Create and push a new **annotated** semantic-version tag such as `v1.2.3` pointing at the intended `main` commit.
3. Manually run the repository's **Release** workflow and provide that existing tag as its `tag` input.
4. Let required CI, exact-tag identity checks, deterministic packaging, checksum validation, provenance attestation, and publication complete.
5. Download the created assets, verify `SHA256SUMS`, and verify at least one archive with `gh attestation verify` before announcing the release.

Do not move or reuse a published tag for different source. If an existing release's assets differ from what the deterministic packager produces for the selected tag commit, the workflow intentionally fails rather than replacing them.
