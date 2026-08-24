# OpenCode status and resume

OpenCode chat history is disposable. AutoDev persists workflow progress beneath:

```text
.autodev-run/current/
```

`run-manifest.json` is the durable checkpoint/invalidation record. `state.json` and the bounded role/verification artifacts provide execution evidence for the shared Python workflow.

## Status

Inside OpenCode:

```text
/autodev-status
```

Outside the OpenCode command frontend, use the first-class CLI:

```text
autodev status
```

Status is read-only. It reports the current issue/run identity, completed and next boundaries, failure information, resume safety, source/PR/CI identity, repair counters, and safe runtime/model metadata.

## Resume

Inside OpenCode:

```text
/autodev-resume
```

Or:

```text
autodev resume
```

Resume validates durable artifact hashes, repository/base/branch identity, source identity, shipped PR/CI proof when present, and execution fingerprints before selecting another role or deterministic stage. It never reconstructs progress from chat history.

Completed reader, synthesizer, planner, implementer, verification, and PR/CI boundaries are not rerun merely because OpenCode restarted.

## Role/runtime changes

OpenCode role/model selection remains owned by `opencode.json` / `opencode.jsonc`. Runtime selection follows the normal AutoDev runtime configuration. Changing an execution-affecting model/runtime for already-completed work requires explicit invalidation before resume; AutoDev does not layer new role output over stale accepted artifacts.

Use the status command to inspect the effective next boundary before resuming.

## Repair counters

Local, semantic, Windows/platform, and CI repair state is durable. An interrupted repair resumes at the matching fixer/verification boundary with the persisted attempt count rather than resetting the budget.

## Missing manifest

AutoDev does not infer trustworthy history for an old/incomplete `.autodev-run/current` directory that lacks the required manifest. Status/resume fail clearly instead of guessing which work already happened.
