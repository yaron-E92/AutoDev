# Semantic verification gate

AutoDev can run an independent semantic verifier after deterministic local verification and before pull-request creation.

The gate is enabled by default when a version-2 provider profile configures the `verifier` role. It can be configured explicitly:

```json
{
  "version": 2,
  "roles": {
    "fixer": {
      "transport": "openai-compatible-chat-completions",
      "model": "provider/fixer-model",
      "base_url": "https://provider.example/v1",
      "api_key_env": "PROVIDER_API_KEY"
    },
    "verifier": {
      "transport": "openai-compatible-chat-completions",
      "model": "provider/verifier-model",
      "base_url": "https://provider.example/v1",
      "api_key_env": "PROVIDER_API_KEY"
    }
  },
  "semantic_verification": {
    "enabled": true,
    "max_schema_retries": 1,
    "max_repair_attempts": 1
  }
}
```

The verifier and fixer roles resolve independently. They may use different providers, models, timeouts, and credentials.

## Gate order

```text
implementation
  -> deterministic verification
  -> semantic verifier
       -> pass: continue
       -> blocked: stop
       -> repair: one targeted fixer patch
            -> deterministic verification again
            -> semantic verifier again
            -> final pass required
  -> PR creation and CI
```

Any later CI repair changes the implementation and invalidates the prior semantic pass. AutoDev reruns deterministic verification and the semantic gate before retrying PR/CI progression.

Plan-only and dry-run implementation modes do not invoke semantic verification.

## Bounded evidence

The verifier receives only bounded, issue-relevant evidence:

- original issue text;
- detectable bullets under an `Acceptance criteria` heading;
- synthesized repository handoff;
- implementation plan;
- changed-file list;
- current tracked and untracked diff;
- deterministic verification artifacts;
- uncertainty or skipped-check notes when present.

Large evidence values are truncated with a SHA-256 marker rather than silently omitted.

## Strict JSON result

The semantic verifier must return JSON only:

```json
{
  "verdict": "pass",
  "requirements": [
    {
      "criterion": "The requested behavior is implemented",
      "status": "met",
      "evidence": [
        "src/Feature.cs",
        "verification/attempt-0.md"
      ]
    }
  ],
  "findings": [
    {
      "severity": "warning",
      "message": "A non-blocking limitation",
      "path": "src/Feature.cs"
    }
  ],
  "repair_brief": ""
}
```

Allowed verdicts:

```text
pass
repair
blocked
```

Allowed requirement statuses:

```text
met
missing
uncertain
```

Allowed finding severities:

```text
blocking
warning
```

A `pass` result is rejected when any requirement is not `met` or any finding is `blocking`. Warnings alone do not block.

A `repair` result must include a non-empty targeted `repair_brief`.

## Malformed output

Malformed or inconsistent output never defaults to pass.

By default AutoDev retries the `verifier` once with the schema validation error and the invalid response. This is a schema-repair retry and does not consume the semantic fixer attempt.

Provider failures remain provider failures. For example, authentication, rate-limit, timeout, and transport errors are recorded using the shared safe invocation metadata instead of being converted to a semantic `blocked` result.

## Semantic repair

When the verifier returns `repair`, AutoDev sends only the issue, plan, current diff, changed files, normalized verifier result, and targeted repair brief to the independently configured `fixer` role.

The fixer must return:

```text
NO_CHANGES_REQUIRED
<short explanation>
```

or:

```text
BEGIN_UNIFIED_DIFF
<applicable unified diff>
END_UNIFIED_DIFF
```

`NO_CHANGES_REQUIRED` is not treated as semantic success. A final semantic pass is still required.

Only one semantic repair attempt is allowed by default.

## Artifacts

Python runner artifacts are written beneath the configured output directory:

```text
verification/semantic-prompt-0.md
verification/semantic-attempt-0.json
verification/repair-brief.md
verification/semantic-repair-prompt.md
verification/semantic-repair-attempt-0.patch
verification/semantic-prompt-1.md
verification/semantic-attempt-1.json
verification/final-verdict.json
model-responses/semantic-verifier-*.txt
model-responses/semantic-fixer-*.txt
model-invocations.json
```

Trusted Windows/Linux workflow artifacts are written beneath:

```text
.codex-run/current/verification/
```

The final semantic result is included in the Python runner's PR body.

## Windows

Semantic verification is automatic when `-ProviderProfile` configures a verifier:

```powershell
scripts\run-real-issue.ps1 `
  -Mode Run `
  -WorkingDirectory "C:\repos\TARGET_REPOSITORY" `
  -Username OWNER `
  -Repo REPOSITORY `
  -ProviderProfile "$env:USERPROFILE\autodev-provider.json"
```

Preserve the legacy PASS/FAIL workflow explicitly:

```powershell
scripts\run-real-issue.ps1 `
  -Mode Run `
  -WorkingDirectory "C:\repos\TARGET_REPOSITORY" `
  -Username OWNER `
  -Repo REPOSITORY `
  -ProviderProfile "$env:USERPROFILE\autodev-provider.json" `
  -DisableSemanticVerification
```

Override the shell workflow repair limit:

```powershell
-MaxSemanticRepairAttempts 0
```

## Linux

```bash
~/repos/AutoDev/scripts/run-real-issue.sh \
  --env ~/automation/state/PROJECT.env \
  --mode Run \
  --owner OWNER \
  --repo REPOSITORY \
  --provider-profile ~/autodev-provider.json
```

Legacy mode:

```bash
--disable-semantic-verification
```

Repair-limit override:

```bash
--max-semantic-repair-attempts 0
```

Equivalent environment variables:

```text
DISABLE_SEMANTIC_VERIFICATION=1
MAX_SEMANTIC_REPAIR_ATTEMPTS=0
```

## Legacy compatibility

`automation.prompt_runner` retains the original verifier parser by default:

```text
PASS
FAIL
```

Semantic JSON parsing is opt-in at the provider boundary:

```text
--verifier-format semantic-json
```

The legacy `promptTemplates/verifier.md` and `promptTemplates/verification-repair.md` files remain unchanged. Semantic runs use `promptTemplates/semantic-verifier.md` and `promptTemplates/semantic-repair.md`.
