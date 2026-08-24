# OpenCode role context sizing

AutoDev keeps exact repository evidence durable while avoiding monolithic role prompts that duplicate the same issue, handoff, plan, diff, or repair text.

Issue #101 was opened after representative TATATORPLAG #29 Planner and Implementer requests were observed at roughly 36K tokens. That is the historical real-run baseline that motivated this change. The optimization does not impose an arbitrary hard token cap. Instead, each heavy role receives a compact control prompt that points to authoritative `.autodev-run/current` artifacts and labels evidence as required or optional.

## Roles

- **Planner** reads `issue.md` and `synthesized-handoff.md` by default. Facts, command groups, and the workspace snapshot are consulted only to validate a concrete path/fact.
- **Implementer** reads the issue and accepted plan once, then inspects only source files needed by that plan. The synthesized handoff is optional rather than a second default copy of repository context.
- **Fixer** receives a pointer to the single targeted repair artifact instead of a second copy of that repair text in `fixer.md`.
- **Verifier** receives exact diff and deterministic/cross-file evidence as durable artifacts. The issue and exact evidence are required; plan and synthesis are loaded only when a concrete scope/repository ambiguity requires them.

This preserves reproducibility: every referenced evidence artifact is durable, and verifier diff/evidence files are generated from the same deterministic collectors previously embedded in the verifier prompt.

## Provider-neutral telemetry

Every prepared Planner, Implementer, Fixer, and Verifier invocation appends a secret-free sizing record to:

```text
.autodev-run/current/context-profile.jsonl
```

The record contains hashes and sizes, not the evidence content itself. It reports:

- the character count and approximate token count of the pre-optimization monolithic prompt generated from the **same real invocation**;
- raw and effective compact-control-prompt size;
- required and optional evidence contribution by artifact;
- a required-evidence and all-listed-evidence upper bound;
- the exact Ponytail prompt-policy mode and its character delta;
- whether Headroom was configured and whether it was actually applied in the direct OpenCode transport.

Approximate tokens deliberately use one provider-neutral estimate of four characters per token. They are for before/after sizing and trend comparison, not billing reconciliation or model-specific context enforcement.

Show the latest heavy-role measurements with:

```bash
python3 -m automation.context_optimization --repo /path/to/target-repo
```

Because AutoDev generates the old monolithic prompt first for measurement and then replaces it before `opencode run`, a representative production run yields a like-for-like **before** measurement without paying for a duplicate model request. The optimized role is the only prompt path sent to OpenCode.

## Ponytail and Headroom

Ponytail-style AutoDev prompt policy is applied to the compact control prompt, and telemetry records the raw/effective character delta. This measures its contribution instead of assuming it saves context.

Headroom is different. In the direct OpenCode role path, OpenCode owns provider transport; AutoDev's provider-layer Headroom proxy is therefore **not** in that request path. Telemetry records `applied_to_opencode_role: false` even when a provider profile enables Headroom. AutoDev does not claim a Headroom token saving that did not actually happen, and exact durable evidence is never silently compressed to manufacture a smaller number.

## Practical target ranges

The historical problem case was approximately 36K tokens for Planner/Implementer requests. These are **operational targets, not hard caps**, for the required-context upper bound reported by the profiler:

| Role | Practical target | Why |
| --- | ---: | --- |
| Planner | 6K-16K approximate tokens | issue + synthesized repository evidence; prior coder plan is normally redundant |
| Implementer | 6K-20K | issue + accepted plan + issue-scoped source inspection |
| Fixer | 2K-10K | one targeted repair artifact + changed source |
| Verifier | 8K-28K | exact issue, diff, deterministic/cross-file evidence; naturally scales with patch size |

A run outside these ranges is a profiling signal, not permission to truncate exact requirements or evidence. Inspect `evidence.components` first: large issue text, oversized synthesis, unusually large diffs, or repeated optional-artifact loading should be addressed at the source.

## Quality comparison

For a representative before/after comparison, retain the normal run outcome together with `context-profile.jsonl` and compare:

- final semantic verdict;
- semantic/local/CI repair counts;
- required/optional evidence loaded;
- baseline versus optimized prepared-role sizes.

A reduction is not accepted as a quality win if it increases repair churn or causes the Verifier to mark previously evidenced requirements uncertain/missing. The existing semantic and deterministic gates remain authoritative.
