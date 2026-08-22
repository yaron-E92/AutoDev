# Install and configure AutoDev

AutoDev's public command is `autodev`. OpenCode is an optional frontend over the same Python workflow core; it is not the owner of AutoDev's repository configuration.

## User installation

From an AutoDev checkout, bootstrap the user-local launcher once:

```text
python -m automation.autodev_cli install --user
```

On POSIX systems the default launcher directory is `~/.local/bin`. On Windows it is the user-local `AutoDev/bin` directory under `LOCALAPPDATA` when available. If that directory is not already on `PATH`, either add it yourself or explicitly allow the installer to add one bounded, reversible profile block:

```text
python -m automation.autodev_cli install --user --add-to-path
```

After opening a new shell, normal usage is:

```text
autodev status
autodev coordinate --arguments 123
autodev resume
autodev privacy ...
autodev queue status
autodev queue next
autodev repo doctor
```

The installer creates one launcher, not one alias/function per subcommand. Re-running installation is idempotent.

To remove the user launcher and only the profile block that AutoDev created:

```text
autodev install --user --uninstall
```

Uninstalling the launcher does not delete target-repository files, `.autodev-run` history, GitHub issues, privacy audit history, or unrelated OpenCode configuration.

## Configure a target repository

From the repository root:

```text
autodev repo install
```

This idempotently creates missing AutoDev-owned repository policy, validates existing policy before GitHub mutations, ensures the canonical queue labels, and installs the optional OpenCode frontend by default. It never opts arbitrary issues into `autodev:managed`.

For a repository that deliberately does not use OpenCode:

```text
autodev repo install --no-opencode
```

Useful maintenance commands are:

```text
autodev repo ensure-labels
autodev repo doctor
autodev repo doctor --fix
```

`doctor` is model-free. It checks the AutoDev CLI/configuration boundary, required external tools, queue policy and label metadata, roadmap validity, privacy policy, a secret-free privacy-grant count, optional OpenCode assets, and the effective OpenCode role/model mapping when OpenCode is enabled. `doctor --fix` repairs AutoDev-owned installation drift; it does not create/broaden privacy consent or rewrite malformed user policy merely to make the check green.

## Repository ownership contract

AutoDev separates configuration, run state, and frontend integration:

```text
.autodev/
  repo.json       # AutoDev repository feature/config ownership
  queue.json      # autonomous queue policy
  roadmap.yaml    # optional deterministic priority overlay
  privacy.json    # repository privacy policy

.autodev-run/
  current/        # durable/resumable run state and evidence

.opencode/
  commands/       # OpenCode-specific AutoDev frontend commands
  agents/         # OpenCode-specific AutoDev role agents
  autodev.py      # temporary compatibility shim
  autodev.ps1     # temporary compatibility shim

opencode.jsonc    # user/OpenCode-owned configuration
```

`opencode.json` / `opencode.jsonc` remains authoritative for OpenCode role/model settings. AutoDev does not copy those values into `.autodev`, because two copies would drift.

`.autodev-run` remains execution state and is never folded into committed configuration.

## Legacy `.opencode/autodev.json` migration

Older target installations used `.opencode/autodev.json` to store the AutoDev checkout and Python launcher. That was generic AutoDev configuration living under the wrong namespace.

The canonical repository installer now removes that file only when it matches the recognized legacy AutoDev-owned schema. It preserves unrelated `.opencode` content and preserves `opencode.json(c)`. The repository-local `.opencode/autodev.py` and `.opencode/autodev.ps1` files remain temporary compatibility shims: they prefer the installed `autodev` command and can fall back to the old configuration only for repositories that have not yet been migrated.

An active `.autodev-run/current` is not deleted or reset during migration.

## Privacy and secrets

Repository setup may create a missing strict privacy policy, but it never writes credentials or privacy grants into the repository. Persistent privacy grants remain user-local. `doctor` reports only status counts (`active`, `expired`, `revoked`) rather than route identities, prompt content, source content, or credentials.

## Scheduler readiness

`autodev scheduler ...` is reserved for the scheduler feature. Installing AutoDev or running `autodev repo install` does **not** silently register unattended execution. Scheduler installation remains an explicit per-repository opt-in.
