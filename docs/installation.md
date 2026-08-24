# Install and configure AutoDev

AutoDev's public command is `autodev`. User installation, target-repository setup, and contributor development are separate workflows.

The CLI is intended to be self-discoverable: `autodev --help` shows normal workflows, and `autodev <command> --help` shows command-specific options, defaults, examples, aliases, and privacy notes where model work may occur.

## User installation

### Current release-bundle installation

Until the native Windows/Linux installers tracked by #184 and #185 are available, the supported end-user distribution is a published GitHub Release bundle. A normal user does not need to clone the AutoDev repository.

Download the assets for one release:

- Linux/other POSIX hosts: `autodev-vX.Y.Z-common.zip`;
- Windows: `autodev-vX.Y.Z-common.zip` plus `autodev-vX.Y.Z-windows.zip` from the same release;
- `autodev-release-manifest.json` and `SHA256SUMS` for verification.

Verify the downloaded assets as described in [`releases.md`](releases.md), then extract them into one **permanent** AutoDev directory. On Windows, overlay the common and Windows archives into that same directory.

The current launcher records the extracted AutoDev root, so do not delete or move that directory while the launcher is installed.

From the extracted release directory, perform the one-time bootstrap:

```text
python -m automation.autodev_cli install --user --add-to-path
```

This bootstrap requires a supported Python interpreter because the current pre-native-package release is still Python-backed. It creates a single user-local `autodev` launcher. After opening a new shell, normal operation uses the installed CLI rather than internal Python module invocations:

```text
autodev --help
autodev doctor
autodev issue-to-pr 123
autodev status
autodev resume
autodev privacy status
autodev queue status
autodev queue next
```

`autodev issue-to-pr ISSUE` is the canonical user-facing spelling for working a specific issue. `autodev coordinate --arguments ISSUE` remains available only as the advanced/integration spelling over the same coordinator.

### Launcher location and PATH

On POSIX systems the default launcher directory is `~/.local/bin`. On Windows it is the user-local `AutoDev/bin` directory under `LOCALAPPDATA` when available.

`--add-to-path` allows AutoDev to add one bounded, reversible profile block. If you prefer to manage `PATH` yourself, bootstrap without that flag and add the reported launcher directory manually.

Re-running launcher installation from the same permanent release directory is idempotent:

```text
autodev install --user
```

The installer creates one launcher, not one alias/function per subcommand.

### Uninstall the launcher

Remove the user launcher and only the PATH profile block AutoDev recorded:

```text
autodev install --user --uninstall
```

Uninstalling the launcher does not delete target-repository `.autodev/` configuration, `.autodev-run/` history, GitHub issues, privacy audit history, scheduler workers, or unrelated OpenCode configuration. After uninstalling the launcher and any scheduler registrations, the extracted product directory can be removed separately if it is no longer needed.

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

`opencode.json` / `opencode.jsonc` remains authoritative for OpenCode role/model settings. AutoDev does not copy those values into `.autodev`, because two copies would drift.

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

Contributors may work directly from a repository checkout. That is intentionally distinct from the end-user release-bundle installation above.

Validate a source checkout with:

```text
python -m compileall -q automation area_reader tests
python -m unittest discover -s tests -v
```

Contributor-only helpers are not advertised as end-user `autodev` commands. See [`../CONTRIBUTING.md`](../CONTRIBUTING.md) for development and architecture guardrails.
