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
```

`reconcile` is state-based and idempotent. It scans current issue/dependency state, so it does not matter whether AutoDev was running when a blocker closed. Closed native blocker relationships are pruned, unresolved blockers keep the issue blocked, and the final blocker closing makes an otherwise eligible managed issue ready.

`status` and `explain` are read-only. They compute current eligibility from GitHub state rather than trusting possibly stale `ready`/`blocked` labels.

None of these commands invokes an LLM or model provider.

## Dependency source of truth

Queue reconciliation uses GitHub's native issue `blocked by` relationships through the issue-dependencies REST API. AutoDev deliberately does not infer dependencies from arbitrary issue prose such as `Depends on #123`; if the native dependency API cannot be read reliably, reconciliation fails closed instead of guessing.

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

This keeps temporary operational conditions such as privacy consent expiry out of GitHub dependency state. Those conditions can move a run into attention-required state without pretending that another issue is a dependency.
