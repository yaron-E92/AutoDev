# Manual and external AutoDev work

AutoDev distinguishes repository work from acceptance criteria that require a human or an external provider. This prevents an issue-to-PR run from creating documentation or placeholder configuration and then claiming that identity validation, purchasing, account provisioning, hardware enrollment, or another external outcome was completed.

## Execution classifications

AutoDev records one of three execution classifications:

- `automatable` — the remaining acceptance criteria can be satisfied through supported repository/GitHub/tool work.
- `mixed` — the issue contains both autonomous and manual/external criteria.
- `manual-external` — the substantive outcome cannot be completed by repository work alone.

Unresolved `mixed` and `manual-external` work transitions to `autodev:attention`. It is a successful **non-runnable** state, not an implementation failure. `autodev:managed` remains the operator's authorization, while stale `autodev:running`, `autodev:ready`, and dependency-derived `autodev:blocked` labels are cleared for that attention state.

## Explicit operator declaration

Known manual work can be declared in the issue body without relying on prose heuristics:

```text
<!-- autodev:execution=manual-external -->
```

An explicit mixed declaration must include the bounded structured contract so AutoDev does not guess which criteria belong to which side:

```text
AUTODEV_EXECUTION_CLASSIFICATION_JSON
{
  "classification": "mixed",
  "reason": "The repository policy can be prepared, but the external signer must be provisioned first.",
  "autonomous_criteria": [
    "Add deterministic signer-policy validation."
  ],
  "manual_criteria": [
    "Provision the external signing identity."
  ],
  "human_actions": [
    "Complete provider enrollment through the authorized human/provider workflow."
  ],
  "resume_evidence": [
    "Record the non-secret signer/profile identifier after provisioning."
  ],
  "manual_prerequisite_blocks_implementation": true,
  "autonomous_subset_independent": false
}
END_AUTODEV_EXECUTION_CLASSIFICATION_JSON
```

When no explicit declaration exists, Reader receives the same bounded schema and must classify the issue before downstream planning/implementation continues.

## Mixed work and decomposition

If a manual prerequisite blocks useful implementation, AutoDev stops before Implementer/Fixer.

If repository-only work is independently useful, AutoDev recommends a child/follow-up issue rather than silently narrowing the mixed parent issue. The parent remains attention-required until its manual criteria are actually satisfied. This keeps issue completion semantics honest.

## Manual action plan

An attention transition writes:

```text
.autodev-run/current/execution-classification.json
.autodev-run/current/manual-action-plan.md
```

The plan lists:

- autonomous criteria, if any;
- manual/external criteria;
- concrete human next actions;
- secret-free evidence required to resume;
- whether decomposition is recommended.

No implementation PR is created merely to document the manual task.

## Signaling manual completion

AutoDev does not scrape arbitrary comments or prose and does not infer that documentation means the external action happened.

When the declared manual prerequisite is complete **and repository work remains**, add this explicit marker to the issue body:

```text
<!-- autodev:manual-evidence=complete -->
```

Then reconcile the queue or resume the existing run. AutoDev refreshes the issue body, clears attention for the resumed run, reacquires `autodev:running`, and sends the refreshed issue back through Reader so the remaining work is classified again.

If the issue was fully manual and no autonomous follow-up remains, close the issue instead of adding the marker.

## Evidence safety

Completion evidence is metadata/state proof, not secret material. Acceptable evidence can include non-secret resource/profile identifiers, GitHub environment/variable metadata, linked issue state, or deterministic verification results.

Never put passwords, tokens, credentials, private keys, certificate key material, or other secret values into an issue comment/body or AutoDev run artifact. The classification contract rejects resume-evidence instructions that request such values.

## Example: external Windows publisher identity

An issue like `yaron-E92/events#176` requires third-party publisher identity validation and certificate/signing-authority provisioning. AutoDev should classify that substantive outcome as `manual-external`, produce the action/evidence plan, clear stale running ownership, and stop before Implementer/Fixer.

Repository work that can proceed independently—such as CI, artifact staging, test-signing support, or release-pipeline engineering—belongs in separate automatable issues and is not blocked merely because the public production identity is still pending.
