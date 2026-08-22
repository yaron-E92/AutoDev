# AutoDev autonomous issue queue

AutoDev separates **authorization** from **derived queue state**.

## Labels

- `autodev:managed` — a human/operator authorizes AutoDev to work on the issue autonomously.
- `autodev:ready` — derived: the issue is managed, open, dependency-free, not attention-required, not already claimed/running, and permitted by repository queue policy.
- `autodev:blocked` — derived: the issue is managed and has at least one open GitHub `blocked by` dependency.
- `autodev:attention` — a human must intervene before autonomous execution can continue.
- `autodev:running` — an existing AutoDev claim/run owns the issue; reconciliation will not make it ready again while that label is present.

`autodev:managed` is never added automatically by reconciliation. `ready` and `blocked` are maintained by AutoDev and should not be treated as authorization by themselves.

## Commands

From an installed target repository:

```text
python .opencode/autodev.py queue reconcile
python .opencode/autodev.py queue status
python .opencode/autodev.py queue explain 123
python .opencode/autodev.py queue next
python .opencode/autodev.py queue next --dry-run
```

`reconcile` is state-based and idempotent. It scans current issue/dependency state, so it does not matter whether AutoDev was running when a blocker closed. Closed native blocker relationships are pruned, unresolved blockers keep the issue blocked, and the final blocker closing makes an otherwise eligible managed issue ready.

`status` and `explain` are read-only. They compute current eligibility from GitHub state rather than trusting possibly stale `ready`/`blocked` labels.

`next` reconciles the queue, checks for an existing repository AutoDev run, filters to currently eligible work, excludes active AutoDev PR ownership, applies the optional roadmap ranking, and returns exactly one issue. It does **not** claim or start the issue; the scheduler/dispatcher owns that later boundary.

`next --dry-run` performs the same deterministic ranking using authoritative read-only queue inspection. It does not create/edit labels or prune closed dependency relationships.

All queue commands support `--json`. None of these commands invokes an LLM or model provider.

## Existing runs take precedence

AutoDev v1 permits one active autonomous issue per repository. Before selecting unrelated new work, `queue next` inspects `.autodev-run/current/run-manifest.json` and the durable workflow state.

The outcomes are intentionally explicit:

- `RESUME_EXISTING` — a durable non-terminal run exists, so the dispatcher should resume it first;
- `ATTENTION_REQUIRED` — the existing run is waiting for human/manual/privacy attention, so unrelated work is not selected;
- `RUN_HEALTH_BLOCKED` — the existing run is failed, blocked, unreadable, or otherwise not safe to ignore; surface that health state instead of starting a second issue;
- `SELECTED` — there is no active run and one eligible issue won deterministic ranking;
- `NO_READY_WORK` — no issue is eligible; this is a successful idle outcome, not an error.

A completed durable run does not prevent selection of the next issue.

## Optional roadmap ranking

Repositories may add `.autodev/roadmap.yaml` to express product order without duplicating dependency truth. Version 1 is intentionally small:

```yaml
version: 1
priority:
  - issue: 118
  - issue: 121
  - milestone: MVP
  - label: priority:high
fallback: oldest
```

Rules:

1. GitHub dependency/queue state decides eligibility first.
2. Explicit `issue` entries are the strongest roadmap priority, regardless of where broader rules appear.
3. `milestone` and `label` entries rank eligible matches in their listed order.
4. An issue matching no roadmap entry falls through to `oldest`.
5. Ties are deterministic: oldest `createdAt`, then issue number.
6. A roadmap match can never override blockers, `autodev:attention`, `autodev:running`, repository autonomous-execution policy, closed state, or an existing AutoDev run/PR.
7. Malformed/unsupported roadmap data fails safely with an actionable error; AutoDev never guesses a ranking from invalid configuration.

The v1 parser accepts only the documented mapping/list shape and plain or quoted scalar values. This is deliberate: the roadmap is a tiny ranking contract, not a general-purpose workflow language.

`queue next` explains whether the winner came from `roadmap:issue`, `roadmap:milestone`, `roadmap:label`, or the `oldest` fallback. Roadmap candidates that look high-priority but are ineligible are reported as skipped when useful.

## Dependency source of truth

Queue reconciliation uses GitHub's native issue `blocked by` relationships through the issue-dependencies REST API. AutoDev deliberately does not infer dependencies from arbitrary issue prose such as `Depends on #123`; if the native dependency API cannot be read reliably, reconciliation fails closed instead of guessing.

The roadmap is **not** a dependency graph. Never copy `blocks` / `blocked by` relationships into `.autodev/roadmap.yaml`.

## Repository policy

A repository can exclude all issues from autonomous execution without removing `autodev:managed` authorization:

```json
{
  "version": 1,
  "autonomous_execution": false
}
```

Save this as `.autodev/queue.json`.

When autonomous execution is disabled, reconciliation removes `autodev:ready` from managed issues but preserves `autodev:managed`, dependency state, attention state, and unrelated labels. Re-enabling the policy and reconciling restores the derived ready state where appropriate.

## Precedence

For an issue participating in the queue, reconciliation uses this order:

1. closed → not active;
2. unmanaged → not authorized;
3. one or more open blockers → `autodev:blocked`;
4. `autodev:attention` → not ready;
5. `autodev:running` → not ready;
6. repository autonomous execution disabled → not ready;
7. otherwise → `autodev:ready`.

Selection then adds these repository-level gates without changing issue eligibility truth:

1. existing durable run/attention/health state wins before unrelated work;
2. an open AutoDev PR for an otherwise ready issue excludes that issue;
3. roadmap ranking applies only to the remaining eligible set;
4. unmatched work falls back to oldest-first;
5. empty eligible set returns `NO_READY_WORK`.

This keeps temporary operational conditions such as privacy consent expiry out of GitHub dependency state. Those conditions can move a run into attention-required state without pretending that another issue is a dependency.
