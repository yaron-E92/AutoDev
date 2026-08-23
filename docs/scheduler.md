# Autonomous scheduler

AutoDev can opt an individual repository into unattended issue-to-PR execution without putting queue, privacy, resume, or implementation logic into cron jobs or Task Scheduler actions.

Scheduling is **explicitly opt-in per repository**. Installing the `autodev` CLI or running `autodev repo install` does not schedule anything.

## Prerequisites

From the target repository:

```text
autodev install --user --add-to-path
autodev repo install
```

Commit and push the repository-owned `.autodev/` policy before installing the scheduler. The dedicated worker is cloned from the repository remote, so AutoDev refuses scheduler installation when the worker does not contain the same configured repository policy as the interactive checkout.

The queue policy must allow autonomous execution. Issues still require the human-owned `autodev:managed` authorization from the queue model; scheduler installation does not enroll arbitrary issues.

## Install

```text
autodev scheduler install
```

The default cadence is 15 minutes. Override it with:

```text
autodev scheduler install --cadence-minutes 30
```

AutoDev chooses a native user scheduler:

- Linux/Unix: `systemd --user` when a user manager is available;
- Linux/Unix fallback: the user's crontab;
- Windows: Task Scheduler.

A backend may be selected explicitly for diagnostics or controlled deployments:

```text
autodev scheduler install --backend systemd-user
autodev scheduler install --backend cron
autodev scheduler install --backend windows-task
```

`launchd` is not part of v1.

## What the native task does

The generated timer, cron entry, or Windows task contains no AutoDev workflow policy. It wakes exactly the shared dispatcher, conceptually:

```text
autodev scheduler run-once --registration <user-local-registration>
```

The shared Python dispatcher then uses the existing queue selector and issue-to-PR coordinator. This preserves the same dependency reconciliation, roadmap ranking, manual/external classification, privacy gate, repair budgets, verification, durable resume, commit, and PR contracts as interactive AutoDev.

## Dedicated worker

Scheduled work never operates in the checkout from which `scheduler install` was run. AutoDev creates and reuses a dedicated clone under user-local state:

```text
~/.autodev/workers/<owner>/<repo>/
```

The scheduler registration is stored separately under:

```text
~/.autodev/schedulers/<owner>/<repo>/registration.json
```

Before starting new work, the worker fetches the remote, verifies that no unexpected local changes are present, checks out the recorded default branch, and fast-forwards it to the corresponding remote branch.

AutoDev does **not** run `git reset --hard` or `git clean` to make a scheduler tick succeed. Unexpected worker modifications stop the tick and require inspection.

A durable in-progress AutoDev run is different: its checkpointed branch and patch are preserved so the shared resume path can continue it before unrelated new work is selected.

## One tick

A scheduled tick follows this boundary:

```text
same-machine lock
  -> validate/update dedicated worker
  -> reconcile queue and inspect durable run
  -> resume existing run first, when applicable
  -> otherwise select the next eligible issue
  -> NO_READY_WORK: successful fast exit
  -> invoke the existing issue-to-PR coordinator headlessly
  -> persist scheduler outcome
```

Queue selection itself remains model-free. If nothing is runnable, the tick ends before the coordinator or any model route is invoked.

The existing coordinator remains authoritative for manual/external classification and privacy. In particular:

- unresolved manual/external work becomes `ATTENTION_REQUIRED` and does not enter an Implementer/Fixer loop;
- a valid time-bounded privacy grant can authorize an unattended route;
- missing, expired, invalidated, or forbidden consent stops before repository/prompt content is sent to that route;
- a headless scheduler tick can consume an existing valid grant but cannot create, widen, or renew consent.

## Overlap and ownership

V1 prevents overlapping ticks for the same repository on one machine with a non-blocking user-local file lock. A second native tick exits successfully without starting another issue.

V1 does **not** implement cross-machine distributed claiming. Until that follow-up exists, configure **one autonomous scheduler owner machine per target repository**. Running autonomous schedulers for the same repository on multiple machines is unsupported even though ordinary interactive AutoDev use remains possible elsewhere.

## Status

```text
autodev scheduler status
autodev scheduler status --json
```

Status reports the configured backend, native-backend state, cadence, dedicated worker, and the last recorded dispatcher outcome.

Common outcomes include:

```text
NO_READY_WORK
OVERLAP_SUPPRESSED
ATTENTION_REQUIRED
DISPATCHED
RUN_HEALTH_BLOCKED
```

`NO_READY_WORK`, `OVERLAP_SUPPRESSED`, and `ATTENTION_REQUIRED` are expected successful scheduler outcomes. They do not mean an implementation crashed.

## Run one tick manually

For setup validation or diagnostics:

```text
autodev scheduler run-once
```

This uses the same registration, lock, worker, queue selection, privacy rules, and coordinator as a native scheduled invocation. It is not a second workflow implementation.

## Uninstall

```text
autodev scheduler uninstall
```

Uninstall removes only the native registration and AutoDev scheduler metadata for that repository. Cron removal is bounded to the AutoDev-managed marker block and preserves unrelated user cron entries.

The dedicated worker is intentionally left in user-local state rather than being recursively deleted during uninstall. This avoids destroying unexpected or diagnostically useful local state; it can be inspected and removed manually once no durable run or user work is needed.

## Paths with spaces

Native task generation quotes the canonical launcher and registration path for the target scheduler. The scheduler may therefore be installed when the AutoDev launcher, user profile, registration directory, or worker path contains spaces.
