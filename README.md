# AutoDev

AutoDev is a resumable issue-to-PR automation system. Python owns workflow state and deterministic transitions; model runtimes are isolated behind role boundaries. The supported user-facing entrypoint is the `autodev` CLI.

## Install

From an AutoDev checkout:

```text
python -m automation.autodev_cli install --user
```

Add the launcher directory to `PATH`, or let AutoDev add its bounded user-profile block:

```text
python -m automation.autodev_cli install --user --add-to-path
```

Then configure a target repository:

```text
autodev repo install
```

OpenCode assets are installed by default. Use `autodev repo install --no-opencode` for repositories that deliberately do not use OpenCode.

See `docs/installation.md` for the ownership and uninstall contract.

## Run an issue

```text
autodev coordinate --arguments 123
```

Resume durable state after an interruption:

```text
autodev resume
```

The coordinator owns Reader, Synthesizer, Planner, Implementer, Fixer, Verifier, deterministic verification, optional Windows verification, semantic repair, PR creation, CI observation, and durable terminal state. AutoDev does not merge its pull request automatically.

With OpenCode installed, the equivalent frontend commands are:

```text
/autodev-issue-to-pr 123
/autodev-resume
```

OpenCode role/model mappings come from `opencode.json` / `opencode.jsonc`. AutoDev does not maintain a second OpenCode model-routing configuration.

## Autonomous queue and scheduler

Queue operations are first-class CLI commands:

```text
autodev queue status
autodev queue next
autodev queue reconcile
```

Scheduler installation is an explicit per-repository opt-in:

```text
autodev scheduler install
autodev scheduler status
autodev scheduler health
autodev scheduler run-once
autodev scheduler uninstall
```

Roadmap priority, distributed claims, resumable runs, privacy readiness, and terminal attention states remain deterministic scheduler inputs. See `docs/queue.md` and `docs/scheduler.md`.

## Privacy

AutoDev defaults to strict handling of repository content. Provider/runtime authorization is checked before model work, and persistent grants are explicit, scoped, revocable, and user-local.

```text
autodev privacy ...
```

See `docs/privacy.md` for policy, consent, grants, and enforcement details.

## Verification

The normal workflow can include:

```text
implementation
  -> local deterministic verification
  -> required platform verification, when configured
  -> semantic verifier
  -> targeted repair when needed
  -> PR / CI
```

Semantic verifier output is strict JSON. Current semantic prompt templates are `promptTemplates/semantic-verifier.md` and `promptTemplates/semantic-repair.md`. See `docs/semantic-verification.md` and `docs/windows-verification.md`.

## Architecture

Production Python is split into responsibility-oriented modules under `automation/` and `area_reader/`. Permanent architecture tests reject giant modules, local import cycles, retired module paths, stale maintained-doc references, issue-migration scaffolding, and chunk artifacts.

See `docs/python-architecture.md`.

## Development

Run the maintained Python checks with:

```text
python -m compileall -q automation area_reader tests
python -m unittest discover -s tests -v
```

Repository CI additionally checks Linux and Windows Python matrices, canonical CLI smoke tests, workflow references, shell syntax, release reproducibility, version intent, repository hygiene, and exact-source Windows verification.

Contribution guidance lives in `CONTRIBUTING.md`.
