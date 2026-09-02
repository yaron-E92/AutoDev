# Install and configure AutoDev

AutoDev's public command is `autodev`. User installation, target-repository setup, and contributor development are separate workflows.

The CLI is intended to be self-discoverable: `autodev --help` shows normal workflows, and `autodev <command> --help` shows command-specific options, defaults, examples, aliases, and privacy notes where model work may occur.

## User installation

Published releases provide native **x86-64** packages containing AutoDev and its Python runtime. End users do not need a source checkout, system Python, `pip`, or a virtual environment.

Verify release checksums/provenance first; see [`releases.md`](releases.md).

### Windows MSI

Download:

```text
AutoDev-X.Y.Z-Setup.msi
```

Run the MSI normally. It is a per-user installer, requires no administrator elevation, installs the product under the current user's local application-data `Programs/AutoDev` directory, registers AutoDev with Windows Installed Apps, and adds the product directory to the **user** `PATH`.

Open a new shell after installation and verify:

```text
autodev --version
autodev --help
autodev doctor
```

Install a newer MSI to upgrade. AutoDev uses one stable Windows Installer upgrade identity across versions; installer replacement is scheduled transactionally so a failed upgrade can roll back to the previously installed product. The release CI also verifies that a rejected/corrupt upgrade does not destroy the working installation.

Uninstall through **Settings > Apps > Installed apps > AutoDev**. MSI uninstall removes the installed product and its installer-owned PATH/registry entries. It intentionally does not remove user AutoDev state or target-repository state.

### Debian / Ubuntu

Download:

```text
autodev_X.Y.Z_amd64.deb
```

Install with the package manager:

```text
sudo apt install ./autodev_X.Y.Z_amd64.deb
```

The package installs the self-contained product under `/opt/autodev` and the canonical command at `/usr/bin/autodev`. It declares the core external runtime dependencies (`git`, `gh`, and the platform C runtime) rather than hiding them inside scripts.

Upgrade by installing the newer downloaded package with the same command. Uninstall with:

```text
sudo apt remove autodev
```

If a distro/package-manager failure prevents an upgrade from completing, reinstall the previously downloaded known-good `.deb` with `sudo apt install ./<previous-package>.deb`. AutoDev configuration and run state live outside `/opt/autodev`, so package replacement does not require deleting them.

### Fedora / RPM-family Linux

Download:

```text
autodev-X.Y.Z-1.x86_64.rpm
```

Install with:

```text
sudo dnf install ./autodev-X.Y.Z-1.x86_64.rpm
```

The RPM uses the same `/opt/autodev` payload and `/usr/bin/autodev` command as the Debian package. Upgrade by installing the newer RPM through `dnf`; uninstall with:

```text
sudo dnf remove autodev
```

If an RPM-family package transaction cannot complete the upgrade, reinstall or downgrade to the previously downloaded known-good RPM through the package manager. User/repository state is not stored in the package payload and remains available to the recovered version.

### What native installation does not do

Native package installation, upgrade, and removal do not silently:

- enable an AutoDev scheduler or recurring task;
- delete user AutoDev configuration, `~/.autodev/` privacy grants, or privacy audit state;
- delete target-repository `.autodev/` policy/configuration;
- delete target-repository `.autodev-run/` checkpoints/evidence;
- rewrite unrelated `opencode.json` / `opencode.jsonc` settings.

If you explicitly installed a repository scheduler, remove that registration before removing the CLI:

```text
autodev scheduler uninstall
```

The package manager owns only the installed product files and launcher registration. Persistent user/repository state remains separately owned so upgrades and reinstalls are recoverable.

### External runtime integrations

The native package contains AutoDev's Python runtime. It does **not** bundle every external tool or model provider. Core GitHub workflows use `git` and the GitHub CLI (`gh`). Linux package managers resolve those declared dependencies. On Windows, install missing external tools normally and use:

```text
autodev doctor
autodev doctor --fix
```

`opencode` remains the default role runtime but is separately configured; native installation does not overwrite OpenCode user configuration.

### Source/release ZIPs

The release still carries common and Windows ZIP snapshots for source inspection, reproducibility, advanced/manual workflows, and contributor use. They are no longer the preferred end-user installation path. Normal installed operation should start with the MSI, DEB, or RPM and then use only the public `autodev` command surface.

## Configure a target repository

Once `autodev` is installed, move to a repository that AutoDev should manage and run:

```text
autodev repo install
autodev doctor
```

Repository setup idempotently creates missing AutoDev-owned policy, validates existing policy before GitHub mutations, ensures the canonical queue labels, and installs the optional OpenCode frontend by default. It never opts arbitrary issues into `autodev:managed` and it never installs an autonomous scheduler implicitly.

For a repository that deliberately does not use OpenCode:

```text
autodev repo install --no-opencode
```

Useful maintenance commands are:

```text
autodev repo ensure-labels
autodev doctor
autodev doctor --fix
```

`autodev repo doctor` is a supported alias of the canonical top-level `autodev doctor` spelling.

`doctor` is model-free. It checks the AutoDev CLI/configuration boundary, required external tools, queue policy and label metadata, roadmap validity, privacy policy, a secret-free privacy-grant count, optional OpenCode assets, and the effective OpenCode role/model mapping when OpenCode is enabled. `doctor --fix` repairs AutoDev-owned installation drift; it does not create/broaden privacy consent or rewrite malformed user policy merely to make the check green.

Normal commands after repository setup include:

```text
autodev issue-to-pr 123
autodev status
autodev resume
autodev privacy status
autodev queue status
autodev queue next
```

`autodev issue-to-pr ISSUE` is the canonical user-facing spelling for working a specific issue. `autodev coordinate --arguments ISSUE` remains available only as the advanced/integration spelling over the same coordinator.

AutoDev-created PRs always carry exactly one version intent:

```text
+semver: major
+semver: minor
+semver: patch
+semver: none
```

Resolution is deterministic:

1. one explicit `+semver:` directive in the source issue body;
2. `autodev issue-to-pr ISSUE --semver INTENT` when the issue has no directive;
3. repository `.autodev/repo.json` `default_semver_intent`;
4. built-in fallback `patch`.

If the issue already owns an intent, a conflicting `--semver` fails closed instead of silently replacing it. Duplicate/conflicting issue directives also fail before AutoDev marks the issue running.


## Runtime and provider configuration

`opencode` is the default role runtime. Runtime selection precedence is:

1. explicit `--runtime`;
2. `AUTODEV_ROLE_RUNTIME`;
3. repository `.autodev/config.json` `role_runtime`;
4. user AutoDev configuration `role_runtime`;
5. `opencode`.

Inspect the effective OpenCode role/model mapping with:

```text
autodev models
```

AutoDev also supports machine/user-local named model profiles. Explicit repository `opencode.json` / `opencode.jsonc` agent mappings remain authoritative over AutoDev profile values for those roles; a repository-selected or user-wide AutoDev profile fills roles that would otherwise inherit from OpenCode defaults. AutoDev does not copy machine-local profile choices into repository `.autodev/` policy.

Use `autodev config path`, `autodev config show`, and `autodev config profile ...` to manage that layer. See [`configuration.md`](configuration.md) for the complete schema, platform paths, copyable defaults/examples, per-repository overrides, and model-routing precedence.

## Repository ownership contract

AutoDev separates configuration, run state, and frontend integration:

```text
.autodev/
  repo.json       # AutoDev repository feature/config ownership, including default_semver_intent
  queue.json      # autonomous queue policy
  roadmap.yaml    # optional deterministic priority overlay
  privacy.json    # repository privacy policy

.autodev-run/
  current/        # durable/resumable run state and evidence

.opencode/
  commands/       # OpenCode-specific AutoDev frontend commands
  agents/         # OpenCode-specific AutoDev role agents

opencode.jsonc    # user/OpenCode-owned configuration
```

`.autodev-run` remains execution state and is never folded into committed configuration.

## Privacy and secrets

Repository setup may create a missing strict privacy policy, but it never writes credentials or privacy grants into the repository. Persistent privacy grants remain user-local. `doctor` reports only status counts (`active`, `expired`, `revoked`) rather than route identities, prompt content, source content, or credentials.

Commands that can invoke configured model providers enforce the repository privacy policy before model work. Use `autodev privacy --help` to inspect consent/grant commands. Headless scheduler runs may consume an existing valid grant but cannot manufacture a new consent grant.

## Scheduler readiness

Installing AutoDev or running `autodev repo install` does **not** register background work. Autonomous scheduling remains an explicit per-repository opt-in:

```text
autodev scheduler install
autodev scheduler status
```

Use `autodev scheduler --help` for backend choices, cadence defaults, health, notifications, worker identity, one-shot execution, and uninstall commands. See [`scheduler.md`](scheduler.md).

## Contributor development

Contributors may work directly from a repository checkout. That is intentionally distinct from native end-user installation.

Source-development checks for a source checkout are:

```text
python -m compileall -q automation area_reader tests
python -m unittest discover -s tests -v
```

Contributor-only helpers are not advertised as end-user `autodev` commands. See [`../CONTRIBUTING.md`](../CONTRIBUTING.md) for development and architecture guardrails.
