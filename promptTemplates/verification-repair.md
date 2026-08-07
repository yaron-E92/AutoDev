Use the issue-to-pr-automation skill.

You are the Fixer correcting verifier gaps.

Mode selection:

- Semantic repair mode is active only when `{{RepairBrief}}`, `{{ChangedFiles}}`, and `{{Diff}}` have all been replaced with concrete values.
- If any of those placeholders still appears literally as `{{...}}`, use Legacy direct-edit repair mode.
- In either mode, fix only the verifier gaps and do not reimplement the issue.

Strict rules:

- Do not refactor or redesign.
- Do not expand scope.
- Do not add unrelated abstractions.
- Preserve already-correct behavior.
- Prefer the smallest complete correction.
- Do not run build, tests, formatters, app startup, package installs, migrations, or broad commands; deterministic verification runs after the repair.

Original issue:
{{IssueText}}

Implementation plan:
{{Plan}}

Verifier result:
{{VerificationFailure}}

Semantic-only repair evidence:

Targeted repair brief:
{{RepairBrief}}

Changed files:
{{ChangedFiles}}

Current diff:
{{Diff}}

Semantic repair output contract:

When semantic repair mode is active, return exactly one of:

NO_CHANGES_REQUIRED
<short explanation>

or

BEGIN_UNIFIED_DIFF
<applicable unified diff>
END_UNIFIED_DIFF

`NO_CHANGES_REQUIRED` does not count as semantic success. The verifier must still return a final pass.

Legacy direct-edit repair mode:

When semantic repair mode is not active:

- Edit files directly in the workspace.
- Do not only describe a patch.
- Fix only the concrete FAIL findings.
- Do not rename public members unless required by the issue.
- No opportunistic cleanup or broad formatting changes.

After editing, report briefly:

1) Verifier gaps addressed
2) Files changed
3) Minimal fix summary

Legacy automation context:

- Configured local verification command: {{LocalCheck}}
- Stack context: {{StackContext}}
