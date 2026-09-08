# Operator-directed revision

`autodev revise` changes the accepted requirement interpretation or implementation direction of an existing resumable run without throwing away useful implementation work.

It is intentionally distinct from both repair and restart:

- **repair** responds to a verifier/local/CI defect inside the currently accepted requirement and plan; repair budgets apply normally;
- **revision** is explicit operator authority that supersedes some of the accepted requirement/plan interpretation while preserving the existing implementation as input;
- **restart** discards the current run boundary and prepares the issue again from a clean base. Revision does not do this by default.

## Commands

Use inline operator instructions:

```text
autodev revise --instructions "Keep this issue limited to centralizing artifact paths; do not implement signing or release orchestration."
```

Use a UTF-8 file:

```text
autodev revise --instructions-file correction.md
```

Explicitly adopt the current GitHub issue body:

```text
autodev revise --refresh-issue
```

The refreshed issue and manual operator authority can be combined:

```text
autodev revise --refresh-issue --instructions "Preserve compatible work, but remove the CI signing choreography."
```

`--refresh-issue` is an explicit authority boundary. Ordinary `autodev resume` continues to use the issue artifact already checkpointed in the run; it does not silently adopt later GitHub edits. When a refresh changes the issue source, `autodev revise` prints the previous and current SHA-256 identities before model work resumes.

## Workflow

A prepared revision invalidates the accepted Synthesizer checkpoint and all dependent stages while retaining the Reader/repository evidence and current implementation/worktree. AutoDev then continues through the existing workflow engine:

```text
current implementation/worktree
        +
current authoritative issue
        +
operator revision instructions
        +
existing Reader evidence
        ↓
   Synthesizer
        ↓
  delta Planner
        ↓
   Implementer
        ↓
deterministic verification
        ↓
 semantic verification
        ↓
      PR / CI
```

No permanent revision-specific model role is introduced. The existing Synthesizer reconciles authority and current implementation evidence; the existing Planner produces a delta plan; the existing Implementer applies that delta in place.

The Planner retains the ordinary six-section deterministic plan contract. During an active revision, its plan must explicitly distinguish work to **PRESERVE**, **REMOVE/REVERT**, **CHANGE**, and **ADD**, and identify verification obligations introduced by the revision.

## Authority conflicts

Manual revision instructions are operator authority, not advisory chat context. The Synthesizer is instructed to reconcile them with the current authoritative issue and repository evidence. If those sources are irreconcilably contradictory, it must emit a bounded `REVISION_CONFLICT:` marker. AutoDev rejects that synthesis as an unresolved authority conflict rather than consuming a protocol-correction or verifier repair attempt and silently choosing one side.

## Durable state and history

Each revision gets a versioned record under:

```text
.autodev-run/current/revisions/<revision-id>/record.json
```

The active revision summary is:

```text
.autodev-run/current/revision.json
```

The durable record contains the run/repository/issue identity, revision trigger and timestamp, original/refreshed issue hashes, manual instruction hash and local artifact, revision-start HEAD and implementation source identity, changed-file identity evidence, superseded stages, archived artifacts, and the role fingerprints used by revised stages.

Before invalidation, AutoDev archives the previous issue, synthesis, plan, implementation completion message, and semantic result when present. Existing run-manifest invalidation records remain the checkpoint authority; the revision archive preserves the superseded human/model artifacts for diagnosis instead of pretending they never existed.

The current implementation is allowed to differ from the prepared base while the revision is waiting to re-run Synthesizer/Planner because that exact source identity is checkpointed as revision input. Additional uncheckpointed worktree drift after revision start remains a resume blocker. Once the revised Implementer is accepted, the normal `patch-applied` source identity becomes authoritative again.

## Repair budgets

Starting a revision does not itself consume local, semantic, or CI repair budget. The old synthesis/plan and their dependent verification evidence are superseded. After the revised Implementer reaches ordinary verification, failures use the normal repair machinery and budgets for that revised implementation.

## Status and interrupted runs

`autodev status` reports the active revision id, trigger (`manual`, `issue-refresh`, or `manual+issue-refresh`), current revised stage, next safe action, and superseded stages. The active revision record is local durable state, so an interruption after revision preparation can continue with ordinary:

```text
autodev resume
```

The revision overlay does not create a second coordinator or workflow engine. Role acceptance, deterministic verification, semantic verification, shipment, privacy authorization, runtime/model selection, and resume fingerprints continue through the existing AutoDev contracts.
