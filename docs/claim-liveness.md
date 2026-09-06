# Distributed claim liveness

AutoDev uses distributed claim refs to prevent two scheduler workers from executing the same issue at the same time. Claim **lease freshness** and workflow **progress/liveness** are deliberately separate concepts.

## Lease freshness is not workflow progress

`heartbeat_at` answers only one question: *is the current worker still actively protecting this issue?*

While one coordinator invocation is running, `HeartbeatLease` may refresh `heartbeat_at` repeatedly so a slow model, build, verifier, or CI wait is not stolen by another worker. Those within-invocation heartbeats do **not** increment the no-progress counter and do not change the durable progress identity.

A heartbeat timestamp by itself is never evidence that the issue-to-PR workflow advanced.

## Durable progress identity

At scheduler-tick boundaries AutoDev derives a bounded SHA-256 progress identity from authoritative durable state for the claimed issue. The identity can include:

- durable workflow status and queue state;
- completed run-manifest stages;
- stage input/output and artifact hashes;
- implementation/base/PR commit SHAs;
- the issue branch head when present;
- bounded repair/verifier attempt counters and deterministic failure fingerprints.

The identity deliberately excludes wall-clock timestamps, claim heartbeat time, arbitrary issue/model prose, volatile log text, invocation output, local paths not needed for run identity, and secrets/customer content. A terminal checkpoint for another issue is not progress or completion evidence for the claimed issue.

Claim metadata stores only the bounded identity and summary needed for liveness decisions:

```text
progress_id
progress_at
progress_summary
no_progress_attempts
liveness_state
```

These fields are additive to the existing v1 claim payload and remain metadata-only.

## No-progress bounds

Existing `.autodev/queue.json` files remain valid. Optional liveness settings are:

```json
{
  "version": 1,
  "autonomous_execution": true,
  "claim_max_no_progress_attempts": 6,
  "claim_max_no_progress_minutes": 360
}
```

`claim_max_no_progress_attempts` defaults to `6` and is bounded to 1–100. `claim_max_no_progress_minutes` defaults to `360` (six hours) and is bounded to 30–10080 minutes. Reaching either bound is enough to classify an unchanged run as stalled.

When a later scheduler tick observes a different durable progress identity, AutoDev resets both the consecutive-attempt count and `progress_at`. Long-lived work may therefore keep running indefinitely as long as it demonstrably advances.

## RUN_STALLED behavior

When the same worker repeatedly reacquires an unchanged active claim and a no-progress bound is reached, AutoDev:

1. publishes the claim as `liveness_state=stalled` with an exact-SHA `--force-with-lease` compare-and-swap;
2. **does not advance `heartbeat_at` merely to mark the claim stalled**;
3. preserves all run/checkpoint/branch/PR state;
4. returns a deterministic `RUN_STALLED` diagnostic, persisted by the scheduler as `SCHEDULER_ERROR`;
5. includes the issue, worker/claim/run identities, last durable progress time, no-progress attempt count, progress summary, and recovery instruction.

A stalled claim is intentionally fail-closed. Another worker receives `STALE_PROTECTED` even after the last heartbeat would otherwise be expired. Hitting the liveness threshold is never permission to duplicate execution.

The normal stale-claim recovery path also refuses to delete a nonterminal stalled claim merely because time passed. If the same issue later has authoritative terminal durable state, reconciliation may remove the stalled claim with the usual exact-ref compare-and-swap.

## Recovery

Do not delete `refs/heads/autodev/claims/issue-<number>` by hand as the normal recovery procedure.

Use the durable worker state instead:

```text
1. Inspect the scheduler diagnostic and dedicated worker.
2. Run `autodev resume` in that dedicated worker when manual recovery is appropriate.
3. Preserve existing checkpoints, implementation branches and PR evidence.
```

If manual resume makes durable progress but remains nonterminal, the owning worker's next scheduler tick observes the changed progress identity, resets the no-progress budget, reactivates the claim and continues normally. If manual recovery reaches a terminal/PR-ready/attention state, stalled-claim reconciliation can safely remove the claim.

A different worker remains blocked throughout this process unless the existing durable evidence is resolved through the supported recovery contract.

## Relationship to heartbeat history

This policy is complementary to the bounded heartbeat representation introduced by #264:

- #264 bounds the **Git history shape** of heartbeat replacement commits;
- #265 bounds the **scheduler liveness behavior** so unchanged work cannot be renewed forever.

Both continue to rely on exact expected-ref `--force-with-lease` semantics for distributed ownership safety.
