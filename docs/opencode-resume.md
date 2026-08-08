# OpenCode status and resume

OpenCode chat history is disposable. AutoDev persists issue-to-PR progress in:

```text
.autodev-run/current/run-manifest.json
```

The manifest is the existing #37 checkpoint/invalidation model. `state.json` remains execution detail for the shared workflow stages, including #69 source/shipped-tree/PR-head/CI proof.

## Status

From a target repository with the AutoDev OpenCode assets installed:

```text
/autodev-status
```

The portable bridge equivalent is:

```text
python .opencode/autodev.py status
```

Use `python3` where appropriate.

Status reports the issue, repository, AutoDev branch, run ID/directory, completed stages, current or failed stage, next valid action, last failure classification/reason, safe-resume result, worktree drift, commit/PR identity, durable repair counters, and the next model-heavy role/model resolved through #66.

Status is read-only. To preview the consequences of changing a completed role configuration:

```text
/autodev-status --invalidate-role planner
```

This shows which #37 checkpoints would be invalidated; it does not invalidate them.

## Resume in a fresh OpenCode process

Start OpenCode in the same target repository and run:

```text
/autodev-resume
```

The resume bridge validates before invoking another model Task:

- the #37 manifest schema and checkpoint artifact hashes;
- repository path, GitHub repository, issue number, prepared base SHA, and AutoDev branch identity;
- local `HEAD` still equals the prepared base used by the OpenCode API-commit workflow;
- direct implementation/repair edits still match the checkpointed source identity;
- if a PR was already created, #69 shipped commit/tree, exact PR head, and terminal-success CI proof are still valid;
- current #66 role-model fingerprints remain compatible with completed work.

If validation fails, resume stops instead of replaying completed work or guessing from chat history.

The manifest-selected dispatch uses the existing coordinator sections:

| Durable next boundary | Resume action |
| --- | --- |
| `repository-read` | reader |
| `handoff-synthesized` | synthesizer |
| `plan-created` | planner |
| `implementation-generated` | implementer |
| `deterministic-verified` | local check |
| `semantic-verified` | verifier/semantic boundary |
| `pr-created` | PR/CI |
| all #37 stages complete, not yet marked ready | defensive `ready` |
| already `ReadyForReview` | complete / existing PR |

Successful reader, synthesizer, planner, implementation/direct-edit, deterministic verification, semantic verification, and PR/CI boundaries are not rerun merely because OpenCode restarted.

## Durable repair counters

Local, semantic, and CI repair attempts are stored in the relevant #37 stage records rather than only in coordinator chat context. If OpenCode is interrupted after a repair request, `/autodev-resume` returns the matching `fixer-local`, `fixer-semantic`, or `fixer-ci` action and the persisted attempt count.

After a fixer edit, downstream deterministic/semantic/PR proof is invalidated through the existing #37 fixer invalidation semantics, while the updated direct-edit source identity is checkpointed again before verification continues.

## Changing a future role model before resume

Role-model selection remains owned by #66 and normal OpenCode configuration. Edit the target repository/user `opencode.json` or `opencode.jsonc`, then inspect:

```text
python .opencode/autodev.py models --repo .
/autodev-status
```

If the changed role has not produced completed work yet, resume can accept the new fingerprint without invalidating earlier stages.

If completed work depends on that role, resume refuses the changed fingerprint until the operator explicitly requests invalidation, for example:

```text
/autodev-resume --invalidate-role planner
```

When invalidation would discard an already-applied direct implementation, AutoDev also requires the worktree to be restored to the prepared base first; it never silently layers a new plan/implementation over stale edits.

Per-run `--model` and `--role-model-profile` flags remain unsupported as defined by #66.

## Common Windows interruption flow

A typical Windows flow is:

```text
1. Launch OpenCode in the target repository.
2. Run /autodev-issue-to-pr 123.
3. Close/restart OpenCode after any checkpointed stage.
4. Launch OpenCode again in the same repository.
5. Run /autodev-status.
6. If Safely resumable is yes, run /autodev-resume.
7. The coordinator enters the existing workflow at the returned next_action.
```

No OpenCode conversation/session identifier is required. The durable `.autodev-run/current` manifest and artifacts are the workflow memory.

## Old runs without a manifest

A `.autodev-run/current` directory created before #63 does not gain a trustworthy #37 checkpoint history retroactively. `/autodev-status` and `/autodev-resume` fail clearly when `run-manifest.json` is missing rather than inferring completed work from filenames or chat history.
