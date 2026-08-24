# Install and configure AutoDev

AutoDev's public command is `autodev`. OpenCode is an optional frontend over the same Python workflow core; it is not the owner of AutoDev's repository configuration.

The CLI is intended to be self-discoverable: `autodev --help` shows the normal workflows, and `autodev <command> --help` shows command-specific options, defaults, examples, aliases, and privacy notes where model work may occur.

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
autodev --help
autodev issue-to-pr 123
autodev status
autodev resume
autodev doctor
autodev privacy status
autodev queue status
autodev queue next
```

`autodev issue-to-pr ISSUE` is the canonical user-facing spelling for working a specific issue. The lower-level `autodev coordinate --arguments ISSUE` spelling remains available for integrations and advanced coordinator use.

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

`autodev scheduler ...` manages unattended execution. Installing AutoDev or running `autodev repo install` does **not** silently register a scheduler. Scheduler installation remains an explicit per-repository opt-in:

```text
autodev scheduler install
autodev scheduler status
```

Use `autodev scheduler --help` for backend choices, cadence defaults, health, notifications, worker identity, one-shot execution, and uninstall commands.

## Contributor development

Source-development checks are intentionally distinct from the public operational CLI. Contributors validate an AutoDev checkout with:

```text
python -m compileall -q automation area_reader tests
python -m unittest discover -s tests -v
```

Contributor-only helpers are not advertised as end-user `autodev` commands. See `CONTRIBUTING.md` for the repository's development and architecture guardrails.
