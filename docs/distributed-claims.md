# Distributed claim heartbeat history

AutoDev represents cross-machine issue ownership with the remote ref:

```text
refs/heads/autodev/claims/issue-<number>
```

The ref points at one metadata-only claim commit. Claim acquisition roots that commit on the repository base used for the claim. Heartbeat renewal **replaces** the current metadata commit while keeping that same stable non-claim parent; it does not append a new heartbeat generation to the claim branch history.

Conceptually:

```text
repository base
  ├── heartbeat A  (old, becomes unreachable)
  ├── heartbeat B  (old, becomes unreachable)
  └── heartbeat C  <- current claim ref
```

Every replacement still uses `git push --force-with-lease` against the exact claim SHA that the worker previously observed. The history shape therefore does not weaken compare-and-swap ownership: a stale renewer, releaser, or takeover attempt loses if another worker has already changed the remote ref.

For compatibility with claims created by older AutoDev versions, renewal walks through parent commits only while they parse as the same repository/issue/worker/run/claim identity. The first different or non-claim parent is treated as the stable repository parent. As a result, the first successful renewal of a legacy chained claim collapses the reachable heartbeat history back to one metadata commit above the stable base.

Old replacement commits become unreachable from the claim ref and are eligible for normal Git garbage collection. Claim payload/schema semantics are unchanged: renewal changes heartbeat state and the commit SHA while preserving the repository, issue, worker, run, claim, acquisition time, and lease duration.
