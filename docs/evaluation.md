# AutoDev evaluation harness

AutoDev's evaluation harness compares workflow configurations through the same provider/profile and runner path used operationally. It does not call Ollama, Groq, OpenRouter, Codex, or Headroom through evaluation-specific transports.

The default path is replay-only and makes no provider calls:

```text
python -m automation.eval_harness \
  --profile legacy-command \
  --profile mixed-groq-openrouter-semantic
```

Results are written beneath the gitignored `.benchmark-results/` directory:

```text
.benchmark-results/<timestamp>/
  aggregate.json
  comparison.md
  <case>/<profile>/result.json
```

## Corpus and profiles

The checked-in versioned inputs are:

```text
benchmarks/eval/cases.json
benchmarks/eval/profiles.json
```

Cases record the issue text, pinned target/base identity, expected relevant areas, expected verification, allowed/expected paths, forbidden paths, no-change expectation, tags, and replay source. The repository ships three small replay cases covering:

- a documentation-only minimal change;
- a targeted Python repair;
- an already-satisfied issue where no code change is correct.

Profiles reference normal operational provider files under `examples/providers/`. The harness does not duplicate provider request construction or secret handling. The checked-in names cover:

```text
legacy-command
local-ollama
ollama-cloud-nemotron-minimax
groq-only
mixed-groq-openrouter
mixed-groq-openrouter-ponytail
mixed-groq-openrouter-semantic
mixed-groq-openrouter-semantic-headroom
```

The mixed variants are intentionally distinct operational configurations:

- `mixed-groq-openrouter`: native prompt policy disabled; semantic verification disabled;
- `mixed-groq-openrouter-ponytail`: native Ponytail-derived prompt policy enabled; semantic verification disabled;
- `mixed-groq-openrouter-semantic`: native prompt policy plus semantic verifier enabled;
- `mixed-groq-openrouter-semantic-headroom`: the semantic profile with optional Headroom compression.

This keeps profile comparisons honest instead of assigning different evaluation names to identical provider configuration.

## Cheap replay comparison

Compare the two checked-in recorded profiles across all replay cases:

```text
python -m automation.eval_harness \
  --profile legacy-command \
  --profile mixed-groq-openrouter-semantic
```

Select individual cases or tags with repeated flags:

```text
python -m automation.eval_harness \
  --profile legacy-command \
  --profile mixed-groq-openrouter-semantic \
  --case python-targeted-repair \
  --tag python
```

Replay mode reads only recorded artifact-shaped metadata, semantic results, run-manifest telemetry, diagnostics, and deterministic diffs. Raw model response text is not a scoring input. If a selected profile has no recording for a case, that profile/case result is `unavailable`; the harness does not borrow output from another configuration.

## Metrics

The JSON result keeps the dimensions separate rather than hiding them behind one score.

### Outcome

- deterministic verification pass;
- semantic verdict and acceptance-requirement counts;
- expected paths missing;
- forbidden paths touched;
- no-change correctness;
- recorded patch-application outcome;
- optional human rating.

### Minimality

- files changed;
- added/deleted lines;
- new files;
- dependency-manifest changes;
- forbidden/unrelated path findings;
- new-abstraction count only when a deterministic recorded source exists; otherwise `unknown`.

### Reliability

- first-pass success;
- deterministic, semantic, and CI repair counts;
- retry counts;
- classified provider failures and HTTP status where available;
- rate/quota failures;
- fallback attempts where recorded;
- blocked runs;
- resume usage where recorded;
- free-model unavailability without paid substitution.

### Efficiency and cost

- stage wall time where recorded;
- model calls by role;
- prompt/output/total tokens where provider telemetry reports them;
- Headroom compression metadata;
- provider-reported cost;
- `unknown` when token or cost data does not exist.

The harness does not invent an estimated dollar amount. Estimated cost remains `unknown` unless a future explicitly documented deterministic rate source is supplied.

### Reproducibility

Results record the target SHA, AutoDev commit when captured, provider profile, safe role/provider/model mapping, sanitized endpoint identity, prompt-policy metadata, semantic settings, Headroom settings, run-manifest schema, OS/tool metadata where recorded, source mode (`replay` or `live`), and a case input hash.

API keys, authorization headers, token values, passwords, cookies, and resolved secret values are excluded/redacted.

## Comparability

A replay is marked not directly comparable when its recorded case version, target SHA, or captured profile fingerprint does not match the selected inputs. Provider/model, prompt-policy, semantic, and Headroom settings remain visible in reproducibility metadata so a report does not hide which configuration actually produced a result.

Aggregate output is grouped by profile and case tag. Per-result reproducibility metadata retains every role's provider transport and model, so provider/model differences remain explicit rather than being collapsed into one opaque score.

Do not infer statistical significance from the three checked-in smoke cases. They exist to make regressions and obvious configuration differences repeatable.

## Live execution safety

Live execution is off by default. It requires both:

```text
--live --apply
```

This is intentionally strict because the normal AutoDev `implement` mode verifies applied working-tree changes. Live evaluation calls:

```text
python -m automation.run_real_issue ... --mode implement
```

through a subprocess timeout. It does not create a PR in the default live mode.

PR-producing evaluation additionally requires:

```text
--sandbox-pr
```

Use `--sandbox-pr` only against a repository/issue explicitly prepared as an evaluation sandbox. AutoDev does not automatically merge evaluation PRs.

Before the first live run, the harness prints the selected profile, provider transport, model, sanitized endpoint, fallback list, selected cases, and whether sandbox PR creation is enabled.

Safety budgets:

```text
--max-cases 3
--max-model-calls 30
--timeout-seconds 3600
--max-reported-cost 2.00
```

The model-call budget uses a conservative bound from configured roles and repair limits before live execution. The reported-cost ceiling stops before a subsequent run once observed provider-reported cost reaches the ceiling; no synthetic price estimate is used for providers that do not report cost.

## Creating a live case

The checked-in corpus is replay-safe. For real evaluations, copy `benchmarks/eval/cases.json` to an uncommitted local file and replace/add a case source with a live target:

```json
{
  "kind": "public",
  "replay_file": "../../tests/fixtures/eval/python-targeted-repair.json",
  "live": {
    "repo_env": "AUTODEV_EVAL_TARGET",
    "github_repo": "OWNER/REPO",
    "issue": 123
  }
}
```

Set `AUTODEV_EVAL_TARGET` to a clean local checkout pinned to the case's intended base. Keep the case issue text/expectations aligned with the referenced GitHub issue.

## Local Ollama comparison

After adding an appropriate live case and setting its local checkout path:

```text
python -m automation.eval_harness \
  --cases-file ./my-live-cases.json \
  --profile legacy-command \
  --profile local-ollama \
  --case my-live-case \
  --live --apply \
  --max-cases 1 \
  --max-model-calls 30
```

This still routes through `automation.run_real_issue` and the operational `examples/providers/ollama-local-all-roles.json` profile.

## Ollama Cloud Nemotron/MiniMax

Cloud use is explicitly opt-in. Complete the existing Ollama Cloud preflight first, then run only against an evaluation target you permit AutoDev to edit:

```text
python -m automation.eval_harness \
  --cases-file ./my-live-cases.json \
  --profile local-ollama \
  --profile ollama-cloud-nemotron-minimax \
  --case my-live-case \
  --live --apply \
  --max-cases 1 \
  --max-model-calls 30 \
  --timeout-seconds 7200
```

Ollama account/plan availability remains an Ollama concern. The harness does not substitute another model if the configured cloud model fails.

## Groq/OpenRouter mixed-provider comparison

For live use, copy the exact mixed provider configuration you want to evaluate and replace `REPLACE_WITH_OPENROUTER_MODEL:free` with the exact OpenRouter `:free` model. Keep `free_only: true`, then point your local evaluation profile manifest at that copied provider file.

For example, compare Groq-only against the plain mixed configuration:

```text
python -m automation.eval_harness \
  --cases-file ./my-live-cases.json \
  --profiles-file ./my-live-profiles.json \
  --profile groq-only \
  --profile mixed-groq-openrouter \
  --case my-live-case \
  --live --apply \
  --max-cases 1 \
  --max-model-calls 30
```

The harness rejects an OpenRouter free comparison unless the model ends in `:free` **and** `free_only` is true. It also rejects configured paid fallback models. If the exact free model is unavailable or the provider returns a quota/rate failure, the run is reported as `unavailable/provider-failed`; the harness never rewrites the selected route to a paid model.

## Headroom and resumability

Headroom measurements are read from the same safe per-invocation compression telemetry added by #36. The harness does not proxy or compress prompts itself.

Resume/retry information comes from #37 run-manifest/invocation artifacts where recorded. The harness does not maintain a second checkpoint system and does not inspect prior model chats.

Compare the Headroom-enabled mixed profile explicitly:

```text
python -m automation.eval_harness \
  --profile mixed-groq-openrouter-semantic \
  --profile mixed-groq-openrouter-semantic-headroom
```

A replay without a recorded run for one of those profiles is reported as unavailable rather than silently borrowing another profile's output.

## Interpreting reports

Prioritize outcome correctness first. A smaller or cheaper run that fails deterministic or semantic verification is not automatically better. After correctness, compare minimality, repair/retry reliability, stage/model-call efficiency, token/compression data, and provider-reported cost.

`comparison.md` intentionally does not select a winner or change AutoDev's recommended production profile. Profile changes remain a maintainer decision informed by the underlying dimensions.
