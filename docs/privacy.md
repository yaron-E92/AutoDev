# Model data privacy

AutoDev enforces repository privacy policy before model-heavy repository content is sent to a configured model route. Privacy is a preflight boundary: if the effective route cannot satisfy the active policy and no permitted explicit consent covers it, AutoDev stops before sending repository/prompt content.

The decision order is intentionally fail-closed:

```text
resolve effective route
  -> apply machine-enforceable privacy controls
  -> verify those controls are effective
       -> verified route still depends on account/project setting?
            -> require a fresh explicit attestation when AutoDev cannot query it
       -> compliant: ALLOW
       -> not enforceable/verifiable/attested: require explicit consent
            -> matching valid consent: ALLOW
            -> consent unavailable/denied: BLOCK
```

Consent is a fallback. AutoDev does not ask you to accept a weaker provider default when it can instead make the request compliant itself.

## Repository policy

A real Git repository defaults to `strict-confidential` unless it explicitly configures another profile. Repository privacy policy belongs in:

```text
.autodev/privacy.json
```

A recommended collaborative/private-project configuration is:

```json
{
  "profile": "strict-confidential",
  "consent_mode": "explicit"
}
```

Supported profiles are:

| Profile | Cloud allowed | Training on customer content | Customer-content retention |
| --- | --- | --- | --- |
| `strict-confidential` | only when compliant/consented | denied | zero |
| `no-training` | only when compliant/consented | denied | bounded or zero allowed |
| `local-only` | no | denied | zero |
| `off` | yes | not checked | not checked |

`local-only` is absolute: a cloud route is blocked rather than offered as a consent exception.

`consent_mode: "deny"` disables consent exceptions even when the profile would otherwise allow explicit consent.

`AUTODEV_PRIVACY_PROFILE` can strengthen repository policy for a run, but it cannot weaken it. For example, a repository declaring `strict-confidential` cannot be changed to `off` merely by setting the environment variable.

Scratch directories that are not Git repositories default to `off` so fixtures/tests do not accidentally acquire cloud-policy behavior. Production Git repositories default to strict handling.

## Inspect privacy state

From the target repository, use the installed CLI:

```text
autodev privacy status
autodev privacy status --json
autodev privacy --help
```

`status` reports grants associated with the current repository identity. Persistent grant data is user-local, not committed repository configuration.

## Automatic enforcement

### OpenRouter direct/provider-backed AutoDev

For `strict-confidential`, AutoDev injects this into every OpenRouter request path before prompt content is sent:

```json
{
  "provider": {
    "data_collection": "deny",
    "zdr": true
  }
}
```

For `no-training`, `data_collection: "deny"` is required; ZDR is added when zero retention is required.

AutoDev verifies that the effective request options contain the required controls before allowing the provider call. When Headroom is in the AutoDev provider path, the same controls are applied to both the direct/fail-open provider and the Headroom proxy provider. Compression must not remove or weaken privacy routing.

### OpenRouter through OpenCode

OpenCode model calls are made by isolated `opencode run --agent autodev-*` subprocesses. Before starting one of those subprocesses AutoDev:

1. resolves the role's effective `provider/model` mapping;
2. uses a runtime `OPENCODE_CONFIG_CONTENT` overlay to add the OpenRouter privacy fields;
3. runs `opencode debug config` with that overlay;
4. checks that the resolved effective configuration contains the required controls;
5. only then launches the role process using that verified environment.

The overlay is runtime-scoped and does not rewrite user-owned OpenCode configuration. It supports the current OpenCode provider-body configuration and the legacy OpenRouter model-option form still recognized by AutoDev.

If higher-precedence/managed configuration removes or overrides the requested privacy values, verification fails and AutoDev does not treat the route as safe.

## Provider-owned/account-level settings

Request-level controls can constrain a downstream model endpoint without proving every account/project setting in the routing service. AutoDev therefore does not equate “the request contains ZDR” with “every service in the path is definitely retaining nothing.”

Where AutoDev cannot query an account/project setting, a repository may carry a fresh, non-secret administrator/user attestation in `.autodev/privacy.json` when collaboration policy permits that evidence.

### OpenRouter account attestation

Example:

```json
{
  "profile": "strict-confidential",
  "consent_mode": "explicit",
  "provider_attestations": {
    "openrouter": {
      "checked_at": "2026-08-14",
      "use_inputs_outputs": "disabled",
      "prompt_logging": "disabled"
    }
  }
}
```

For OpenRouter strict mode, automatic request controls plus a fresh account-setting attestation can allow the route without prompting. If the attestation is absent or stale and the remaining requirement cannot be verified automatically, the route requires explicit consent or blocks before model content is sent.

Attestations are deliberately recorded as attestations rather than machine-verified provider state. They expire after the same review window as built-in provider-policy metadata and must be refreshed after the underlying setting is checked again.

### Groq ZDR attestation

When account-level Groq ZDR has been verified outside AutoDev but cannot be queried by the runner, record:

```json
{
  "provider_attestations": {
    "groq": {
      "checked_at": "2026-08-14",
      "zero_data_retention": "enabled"
    }
  }
}
```

Without that attestation, `no-training` may proceed while the reviewed no-training policy is fresh, but `strict-confidential` requires consent because bounded reliability/abuse retention cannot be ruled out.

### OpenAI API ZDR attestation

For a direct OpenAI API route where eligible organization/project ZDR has been verified outside AutoDev, record:

```json
{
  "provider_attestations": {
    "openai": {
      "checked_at": "2026-08-14",
      "zero_data_retention": "enabled"
    }
  }
}
```

This applies only to the direct OpenAI API route AutoDev identified from the actual endpoint. It must not be reused as proof for a consumer/OAuth product merely because the vendor name is also OpenAI.

### OpenCode OpenAI routes

An OpenCode model ID beginning with `openai/` can involve product/authentication semantics that AutoDev cannot safely infer from the model name alone. AutoDev therefore classifies this as a distinct `openai-opencode` route instead of silently applying direct OpenAI API policy.

If an administrator has verified the effective OpenCode/OpenAI product has the required controls, a fresh attestation can state them explicitly:

```json
{
  "provider_attestations": {
    "openai-opencode": {
      "checked_at": "2026-08-14",
      "training_on_customer_content": "denied",
      "zero_data_retention": "enabled"
    }
  }
}
```

Otherwise strict/no-training policy asks for consent or blocks, depending on run mode and repository policy.

## Reviewed provider-policy classifications

AutoDev keeps time-bounded reviewed provider-policy metadata. Current classifications include:

- Groq: customer content is not used for training; ordinary processing can involve bounded reliability/abuse retention unless ZDR is enabled.
- direct OpenAI API: API content is not used for training by default; ordinary abuse-monitoring retention can exist unless eligible ZDR controls are enabled.
- Ollama Cloud: prompt/response content is processed transiently and not used for training according to the reviewed policy metadata.
- local inference: customer content remains local for purposes of this gate.

Provider-policy metadata is not permanent trust. Once a reviewed policy entry becomes stale, its classification becomes unknown and strict runs fail closed or require permitted explicit consent rather than trusting an old promise indefinitely.

## Explicit consent and persistent grants

If enforcement, verification, and permitted fresh attestations still cannot meet the active policy, AutoDev may request explicit consent through an interactive terminal. The consent view identifies the affected role/routes and the unmet privacy requirement; the default is rejection.

Create or pre-authorize consent from the target repository with:

```text
autodev privacy consent
```

The interactive command can choose one of these durations:

```text
run
24h
7d
30d
until-revoked
```

You can provide the duration explicitly, for example:

```text
autodev privacy consent --duration 7d
autodev privacy consent --duration 24h --scope provider
autodev privacy consent --duration until-revoked --scope exact --role implementer
```

Supported persistent scopes are:

- `configured` — the currently configured consent-required routes selected by the command;
- `provider` — provider/policy scope for the selected consent-required providers;
- `exact` — exactly one selected route; use `--role` when needed to select that route.

`--duration run` is not persistent and requires an active AutoDev run. `24h`, `7d`, `30d`, and `until-revoked` create user-local grants tied to the repository identity and the privacy-policy/route information recorded when consent was granted.

Persistent grants are stored under the user's AutoDev state (by default `~/.autodev/privacy-grants.json`, with restrictive file permissions where supported). They are not written to `.autodev/privacy.json`, source control, prompts, or target-repository run artifacts as secret material.

Inspect or revoke grants with:

```text
autodev privacy status
autodev privacy revoke <grant-id>
autodev privacy revoke --all
```

Grant IDs may be supplied by unique prefix when revoking one active grant.

A headless or scheduled run can consume a matching active grant. It cannot interactively create, widen, renew, or replace consent. Missing, expired, revoked, mismatched, or policy-invalidated consent stops before model content is sent.

Prefer making a route compliant through provider controls or fresh permitted attestations rather than using a consent exception. Use the narrowest scope and duration appropriate to the actual need.

## Scheduler behavior

Scheduler ticks use the same privacy gate as interactive issue-to-PR runs. A scheduler may continue only when every model route it needs is already compliant or covered by a valid grant. Installing a scheduler does not grant consent, and `scheduler run-once` does not bypass privacy policy.

This means unattended operation can remain genuinely unattended during an approved grant window without allowing a headless process to manufacture new authorization after that grant expires.

## Audit trail

Privacy decisions are appended without prompt content or secrets to:

```text
.autodev-run/current/privacy-audit.jsonl
```

Before a current run directory exists they are written under `.autodev-run/privacy-audit.jsonl`.

Records include safe metadata such as:

```text
role
requested/effective route
provider/model
privacy outcome
training classification
retention classification/duration
enforcement state
request/config controls applied
fresh account-level attestations used
policy source/review date
consent scope, if any
```

They do not contain API keys, Authorization headers, prompts, repository source, or hidden reasoning.

## Target repository resolution

Privacy commands and model workflows apply policy for the explicit target repository when supplied by the caller. CLI integrations also support the configured target-repository environment/arguments used by AutoDev; otherwise the active working directory is used. Normal users should run `autodev privacy ...` from the target repository whose grants/policy they intend to inspect or change.

## Important limits

Passing this gate is a technical AutoDev data-handling decision, not a legal-compliance certification. It does not claim GDPR, contractual, export-control, or other legal compliance.

The gate covers model calls made by AutoDev and the isolated OpenCode role subprocesses AutoDev launches. It cannot govern unrelated tools a developer manually invokes outside AutoDev.

A fresh administrator/user attestation is intentionally distinct from machine verification. It exists only for provider/account controls that AutoDev cannot currently query. If collaboration policy requires machine-verifiable controls only, omit attestations and configure `consent_mode: "deny"` as appropriate; an unverified route will block.

“No training” and “zero retention” are separate requirements. Encryption in transit, smaller/compressed prompts, or a provider being called “enterprise” do not by themselves satisfy either requirement.
