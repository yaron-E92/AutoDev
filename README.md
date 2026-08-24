# AutoDev

AutoDev autonomously turns GitHub issues into reviewed pull requests. Python owns deterministic workflow state, checkpoints, verification, repair, GitHub shipment, and resume; model providers run behind explicit role/runtime boundaries.

The supported product surface is the installed `autodev` CLI. OpenCode can be installed as an optional frontend over the same workflow.

## Install AutoDev

For the current release format, install from a published GitHub Release rather than cloning the repository as an end-user workflow:

1. Download `autodev-vX.Y.Z-common.zip` from the release you want to use.
2. On Windows, also download `autodev-vX.Y.Z-windows.zip` from the same release and overlay both archives into one permanent AutoDev directory.
3. Verify `SHA256SUMS` / release provenance as described in [`docs/releases.md`](docs/releases.md).
4. From the extracted release directory, bootstrap the user-local launcher once:

   ```text
   python -m automation.autodev_cli install --user --add-to-path
   ```

5. Open a new shell and use the public CLI:

   ```text
   autodev --help
   autodev doctor
   ```

The release-bundle bootstrap is the temporary installation boundary before the native Windows/Linux installers tracked by #184 and #185 exist. Normal operation after bootstrap uses `autodev`; users do not need to invoke internal Python modules for issue work, resume, setup, queueing, privacy, or scheduling.

For launcher ownership, PATH behavior, uninstall, and release-bundle details, see [`docs/installation.md`](docs/installation.md) and [`docs/releases.md`](docs/releases.md).

## Configure a target repository

From the repository that AutoDev should work on:

```text
autodev repo install
autodev doctor
```

Repository setup creates/validates AutoDev-owned policy, queue labels, and optional OpenCode assets. It does not silently opt issues into autonomous execution or install a scheduler.

OpenCode integration is installed by default. Repositories that deliberately do not use OpenCode can run:

```text
autodev repo install --no-opencode
```

## Work an issue

The canonical interactive issue workflow is:

```text
autodev issue-to-pr 123
```

AutoDev prepares durable run state, executes the configured Reader/Synthesizer/Planner/Implementer/Fixer/Verifier roles as needed, performs deterministic and semantic verification, creates or updates the issue PR, observes CI, and stops in an explicit terminal/attention state. AutoDev does not merge the resulting PR automatically.

Resume an interrupted or waiting run without replaying accepted work:

```text
autodev resume
```

Inspect durable state or effective OpenCode model mappings with:

```text
autodev status
autodev models
```

`autodev coordinate ...` remains a supported advanced/integration spelling. Normal users should prefer `issue-to-pr` and `resume`; `autodev --help` is the canonical command vocabulary.

## Queue and autonomous scheduling

AutoDev separates human authorization from derived queue state. Useful queue commands include:

```text
autodev queue status
autodev queue explain 123
autodev queue next
autodev queue reconcile
```

A repository scheduler is an explicit opt-in:

```text
autodev scheduler install
autodev scheduler status
autodev scheduler health
autodev scheduler run-once
autodev scheduler uninstall
```

The native scheduler only wakes the shared dispatcher. Dependency reconciliation, roadmap ranking, distributed claims, privacy checks, resume behavior, verification, and PR shipment remain in the same AutoDev workflow used interactively.

See [`docs/queue.md`](docs/queue.md) and [`docs/scheduler.md`](docs/scheduler.md).

## Privacy

AutoDev checks provider/runtime privacy authorization before repository or prompt content is sent to model routes. Persistent consent grants are explicit, scoped, time-bounded, revocable, and user-local; headless scheduler runs may consume an existing valid grant but cannot create one.

Start with:

```text
autodev privacy status
autodev privacy --help
```

See [`docs/privacy.md`](docs/privacy.md).

## OpenCode and role runtimes

`opencode` is the default role runtime. When OpenCode is enabled, `opencode.json` / `opencode.jsonc` remains authoritative for role/model mapping; AutoDev does not maintain a second copy.

The optional OpenCode frontend exposes commands such as:

```text
/autodev-issue-to-pr 123
/autodev-resume
/autodev-status
/autodev-models
```

See [`docs/opencode.md`](docs/opencode.md), [`docs/role-runtimes.md`](docs/role-runtimes.md), and [`docs/model-roles.md`](docs/model-roles.md).

## Verification model

A normal issue run can progress through:

```text
implementation
  -> local deterministic verification
  -> required platform verification, when configured
  -> semantic verification
  -> targeted bounded repair when needed
  -> PR / CI
```

Semantic verification uses strict artifacts and bounded repair budgets. Optional exact-source Windows verification can be required by repository policy.

See [`docs/semantic-verification.md`](docs/semantic-verification.md), [`docs/semantic-repair-budgets.md`](docs/semantic-repair-budgets.md), and [`docs/windows-verification.md`](docs/windows-verification.md).

## Architecture

Production Python is split by responsibility under `automation/` and `area_reader/`. At a high level:

- CLI/repository setup owns installation and target-repository configuration;
- workflow orchestration owns deterministic stage sequencing and durable run state;
- role runtimes/providers own bounded model invocation only;
- privacy owns route authorization and consent enforcement;
- queue/scheduler owns deterministic autonomous selection and dispatch;
- verification owns deterministic/platform/semantic evidence and bounded repair;
- GitHub/release integrations own shipment, CI observation, and reproducible release artifacts.

Architecture tests reject oversized responsibility modules, local import cycles, retired module paths, stale maintained-doc references, migration scaffolding, and chunk artifacts.

See [`docs/python-architecture.md`](docs/python-architecture.md).

## Documentation map

- Installation and repository setup: [`docs/installation.md`](docs/installation.md)
- Releases and provenance: [`docs/releases.md`](docs/releases.md)
- Queue policy: [`docs/queue.md`](docs/queue.md)
- Scheduler: [`docs/scheduler.md`](docs/scheduler.md)
- Privacy/consent: [`docs/privacy.md`](docs/privacy.md)
- OpenCode: [`docs/opencode.md`](docs/opencode.md)
- Runtime/model roles: [`docs/role-runtimes.md`](docs/role-runtimes.md), [`docs/model-roles.md`](docs/model-roles.md)
- Python architecture: [`docs/python-architecture.md`](docs/python-architecture.md)
- CI/release policy: [`docs/ci-profiles.md`](docs/ci-profiles.md), [`docs/version-policy.md`](docs/version-policy.md)

## Contributing

Development from a source checkout is a contributor workflow, not the normal user installation path. Contributors run:

```text
python -m compileall -q automation area_reader tests
python -m unittest discover -s tests -v
```

Repository CI additionally checks Linux/Windows Python matrices, canonical CLI smoke tests, workflow references, shell syntax where maintained, release reproducibility, version intent, repository hygiene, and exact-source Windows verification.

See [`CONTRIBUTING.md`](CONTRIBUTING.md).
