# Role-routing benchmark and fallback policy

Issue #97 extends AutoDev's existing replay/live evaluation harness with role-level model-routing evidence. It does not add a second benchmark runner and it does not make a provider mandatory.

The goal is to answer a narrower operational question:

> Which local or genuinely free-cloud candidates have enough observed AutoDev evidence to take routine role work, and where should a frontier OpenAI/Codex route remain an explicit fallback?

The benchmark deliberately separates **configuration metadata** from **observed task evidence**. Merely appearing in a provider profile does not qualify a model.

## What the report adds

Normal evaluation output still contains correctness, semantic verification, minimality, repair counts, provider failures, wall time, token telemetry, Headroom telemetry, and reported cost when available.

Role-routing analysis adds:

```text
.benchmark-results/<timestamp>/
  comparison.md
  aggregate.json
  routing-recommendation.md
  routing-recommendation.json
```

For every Reader, Synthesizer, Planner, Implementer, Fixer, and Verifier candidate, the aggregate records:

- candidate class (`local`, `free-cloud`, `cloud-plan-dependent`, `frontier-baseline`, or other cloud);
- configured runs versus runs in which that role/model was actually observed;
- workflow completion, deterministic pass, semantic pass, provider failures, and downstream repair burden;
- workflow token and elapsed-time evidence when the recorded provider/run exposed it;
- request-size/rate-limit facts from the reviewed `benchmarks/eval/provider-facts.json` snapshot;
- privacy admissibility previews for `no-training`, `strict-confidential`, and `local-only` using the #112 privacy model;
- the source URLs and review date behind provider/request-limit metadata.

No opaque combined quality score is introduced.

## Evidence rules

A candidate is **observed** only when the selected replay/live result contains an actual model invocation for that role.

An unavailable replay remains visible as a configured candidate with zero observed runs. A provider profile containing a placeholder such as:

```text
REPLACE_WITH_OPENROUTER_MODEL:free
```

also remains unqualified. Replace it with a concrete model in a local/uncommitted provider profile and run that exact model before treating it as evidence.

This prevents an unrun local model or a moving OpenRouter free route from becoming a recommendation merely because it is configured.

The current checked-in replay corpus is intentionally small. It is suitable for exercising the evaluation machinery and preserving historical comparisons, not for declaring a universal model winner.

## Provider/request-limit facts

`benchmarks/eval/provider-facts.json` is a reviewed, versioned benchmark input. It is not permanent provider truth.

The 2026-08-14 snapshot records, among other things:

- Groq `openai/gpt-oss-20b` published context/output limits and published Free Plan rate limits;
- OpenRouter free-route daily-request guidance and the requirement to resolve context/capabilities for the exact `:free` model;
- local Ollama request limits as runtime/model/Modelfile-specific rather than assuming an upstream model maximum for a custom local alias;
- Ollama Cloud availability/limits as model/account/plan-specific;
- Codex/OpenAI as a selected-profile/account-allowance-dependent frontier baseline rather than a hard-coded model.

Refresh the snapshot when provider facts materially change. Do not infer a current provider limit from an old benchmark result.

## Privacy comes before routing preference

The #112 privacy gate remains authoritative. The recommendation order is:

```text
privacy admissibility
  -> deterministic correctness
  -> semantic quality
  -> downstream repair burden
  -> candidate cost class
  -> efficiency
```

A candidate that benchmarks well but is not admissible for the repository's active privacy profile is not an automatic route.

The benchmark's privacy matrix is intentionally a **preview**. A real run still executes #112's enforce/verify/attest/consent gate. For example, AutoDev can show that OpenRouter has request-level privacy controls available, while still requiring the real run to verify or attest account-level settings that the benchmark cannot query.

Do not weaken a confidential repository's policy simply to make a benchmark candidate runnable. Use a public/synthetic sandbox or configure the real account-level privacy control instead.

## Cheap replay comparison

Replay makes no live model calls:

```text
python -m automation.eval_harness \
  --profile legacy-command \
  --profile local-ollama \
  --profile groq-only \
  --profile mixed-groq-openrouter-semantic
```

The report will explicitly show which selected profile/case pairs do not have a checked-in replay and which role/candidate combinations therefore still lack observed evidence.

The `benchmark_coverage` section is the acceptance-evidence checklist for the roles that matter most to #97:

```text
Planner
Implementer
Fixer
Verifier
```

Each of those roles is considered covered only after at least one `local` and one `free-cloud` candidate has an observed benchmark run. Missing combinations remain listed rather than being inferred.

## Representative live comparison

Live execution uses the normal operational runner. Prepare an evaluation target as described in `docs/evaluation.md`, pin its base/issue, and use a clean checkout.

For a first local versus free-cloud comparison:

```text
python -m automation.eval_harness \
  --cases-file ./my-live-cases.json \
  --profile local-ollama \
  --profile groq-only \
  --case representative-issue \
  --live --apply \
  --max-cases 1 \
  --max-model-calls 30 \
  --timeout-seconds 7200
```

This exercises Planner, Implementer, Verifier, and—when the implementation needs repair—Fixer through the same provider/profile and workflow paths used outside the benchmark.

A single issue that happens to pass first try does **not** produce Fixer evidence. Add or select a representative repair case rather than inventing a Fixer score.

## Concrete OpenRouter free-model comparison

The checked-in mixed profiles intentionally contain a placeholder because a free model is not stable enough to hard-code as architecture.

Copy the desired operational profile to an uncommitted local file, replace the placeholder with the exact current `:free` model, keep:

```json
{
  "free_only": true
}
```

and point a local evaluation profile at that provider file. Do not add a paid fallback merely to make the benchmark finish.

If the free model is unavailable, rate-limited, rejects AutoDev's request size, or fails the role contract, that is benchmark evidence. The harness records the provider failure rather than silently substituting OpenAI.

## Interpreting the recommendation

A generated role recommendation can have these important states:

```text
qualified-by-observed-workflow-evidence
qualified-evidence-but-strict-confidential-needs-exception
insufficient-evidence
```

`qualified-by-observed-workflow-evidence` means the candidate had observed runs and was automatically admissible in the strict-confidential preview among the candidates being ranked. It does not mean the benchmark corpus is statistically exhaustive.

`qualified-evidence-but-strict-confidential-needs-exception` means there was observed quality evidence, but no observed candidate in that comparison was automatically admissible under the strict preview. The real privacy gate must still allow the route before use.

`insufficient-evidence` means AutoDev refuses to manufacture a recommendation for that role.

## Default/fallback policy after qualification

The intended policy is evidence-driven rather than provider-driven:

```text
Reader/Synthesizer
  -> qualified local candidate by default
  -> qualified free-cloud candidate when local quality/capacity is insufficient

Planner
  -> best privacy-admissible qualified local/free candidate
  -> frontier fallback when no cheaper candidate reliably plans the representative workload

Implementer/Fixer
  -> qualified privacy-admissible local/free candidate when observed correctness and repair burden justify it
  -> explicit frontier fallback for provider failure, repeated repair, request-size/capability failure, or unqualified workload

Verifier
  -> qualified efficient candidate, preferably a different model from the Implementer
  -> never weaken semantic independence simply to save model calls
```

The generated report never rewrites the user's operational model mappings and never performs an automatic paid/frontier substitution. It supplies evidence for a later default/fallback choice; explicit overrides remain available.

## Completing #97 with real evidence

The implementation is ready to record and compare the required candidates, but the acceptance criterion requiring actual local **and** free-cloud observations for Planner, Implementer, Fixer, and Verifier must be satisfied by real model runs. CI intentionally performs no live provider calls.

Before closing #97, run representative live cases until `routing-recommendation.json` reports:

```json
{
  "benchmark_coverage": {
    "complete": true,
    "missing": []
  }
}
```

Then preserve the resulting report or a replay-safe, secret-free capture as benchmark evidence. Do not mark missing combinations complete by hand.
