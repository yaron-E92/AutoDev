# Model data privacy

AutoDev can enforce a repository-level policy before model-heavy repository content is sent to cloud LLMs.

The decision order is intentionally fail-closed:

```text
resolve effective route
  -> apply machine-enforceable privacy controls
  -> verify those controls are effective
       -> verified route still depends on account/project setting?
            -> require a fresh explicit attestation when AutoDev cannot query it
       -> compliant: ALLOW
       -> not enforceable/verifiable/attested: require explicit consent
            -> consent unavailable/denied: BLOCK
```

Consent is a fallback. AutoDev does not ask you to accept a weaker provider default when it can instead make the request compliant itself.

## Repository policy

A real Git repository defaults to `strict-confidential` unless it explicitly configures another profile. Put repository policy in:

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

`AUTODEV_PRIVACY_PROFILE` can strengthen a repository policy for a run, but it cannot weaken it. For example, a repository declaring `strict-confidential` cannot be changed to `off` merely by setting the environment variable.

Scratch directories that are not Git repositories default to `off` so fixtures/tests do not accidentally acquire cloud-policy behavior. Production Git repositories default to strict handling.

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

OpenRouter documents `data_collection: "deny"` as restricting requests to provider endpoints that do not collect user data and `zdr: true` as restricting routing to Zero Data Retention endpoints. AutoDev verifies the effective request options contain the required fields before allowing the provider call.

When Headroom is in the AutoDev provider path, the same controls are applied to both the direct/fail-open provider and the Headroom proxy provider. Compression must not remove or weaken privacy routing.

### OpenRouter through OpenCode

OpenCode model calls are made by isolated `opencode run --agent autodev-*` subprocesses. Before starting one of those subprocesses AutoDev:

1. resolves the role's effective `provider/model` mapping;
2. uses a runtime `OPENCODE_CONFIG_CONTENT` overlay to add the OpenRouter privacy fields;
3. runs `opencode debug config` with that overlay;
4. checks the resolved effective configuration contains the required controls;
5. only then launches the role process using that verified environment.

The overlay is runtime-scoped and does not rewrite a developer's global OpenCode configuration. It supports current OpenCode `providers.*.body` configuration as well as the legacy OpenRouter `provider.*.models.*.options.provider` form.

If a higher-precedence/managed configuration removes or overrides the requested privacy values, verification fails and AutoDev does not treat the route as safe.

## Provider-owned/account-level settings

A request can constrain the downstream model provider while the routing service itself may still have account-level content settings. AutoDev does not equate "the request contains ZDR" with "every service in the path is definitely retaining nothing."

OpenRouter, for example, documents that prompt/completion logging and use of inputs/outputs are opt-in account/workspace settings. The request-level `data_collection` and `zdr` fields constrain downstream endpoints; they do not prove those OpenRouter-owned account settings are disabled.

Where AutoDev cannot query an account/project setting, a private repository may carry a fresh, non-secret administrator/user attestation in `.autodev/privacy.json`. Example:

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

For OpenRouter strict mode, automatic request controls **plus** this fresh account-setting attestation allow the route without prompting. If the attestation is absent or stale, AutoDev asks for explicit consent or blocks before transmitting repository content.

Attestations are deliberately labeled as attestations in the audit trail rather than as machine-verified provider state. They expire after the same review window as built-in provider policy metadata and must be refreshed after the underlying provider/account setting is checked again.

### Groq ZDR attestation

Groq documents inference data as not used for training and offers account-level Zero Data Retention controls. When a collaborator has verified ZDR is enabled but AutoDev cannot query that account setting, record:

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

OpenAI API data is not used for training by default, but ordinary abuse-monitoring logs may retain customer content. Eligible organizations/projects can enable Zero Data Retention. If that setting has been checked outside AutoDev and cannot be queried by the runner, record:

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

Otherwise strict/no-training policy asks for consent or blocks, depending on run mode.

## Other provider policy classifications

AutoDev keeps reviewed provider-policy metadata with a freshness limit. Current classifications include:

- Groq: customer content is not used for training; ordinary processing can involve bounded reliability/abuse retention unless ZDR is enabled.
- direct OpenAI API: API content is not used for training by default; ordinary abuse-monitoring retention can exist unless eligible ZDR controls are enabled.
- Ollama Cloud: prompt/response content is processed transiently and not used for training according to Ollama's current privacy statement.
- local inference: customer content remains local for purposes of this gate.

Provider-policy metadata is time-bounded. Once stale, it becomes unknown and strict runs fail closed/require consent instead of trusting an old promise forever.

## Explicit consent

If enforcement, verification, and permitted fresh attestation still cannot meet the active policy, AutoDev asks for consent when it has an interactive input channel. The prompt identifies the role, route, known training/retention status, and the unmet requirement. The default answer is No.

Non-interactive/headless execution cannot manufacture consent. It blocks before the prompt is sent.

For a deliberately pre-authorized exception in a non-interactive run, use an exact role+route entry:

```powershell
$env:AUTODEV_PRIVACY_CONSENT = "implementer=groq/groq/model-id"
```

Multiple exact entries may be comma-separated. This is intentionally narrow: authorizing one role/route does not authorize another role or a later provider/model change.

Prefer fixing/enabling a provider's privacy controls or refreshing a verified account-setting attestation rather than relying on consent exceptions.

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

## Direct/headless target repository resolution

The privacy layer uses the explicit target repository when supplied by the caller. For CLI workflows it also supports `AUTODEV_TARGET_REPO` and target-repository arguments such as `--repo` / `--working-directory`; otherwise the active working directory is used.

## Important limits

Passing this gate is a technical AutoDev data-handling decision, not a legal-compliance certification. It does not claim GDPR, contractual, export-control, or other legal compliance.

The gate covers model calls made by AutoDev and the isolated OpenCode role subprocesses AutoDev launches. It cannot govern unrelated tools a developer manually invokes outside AutoDev.

A fresh administrator/user attestation is intentionally distinct from machine verification. It exists only for provider/account controls that AutoDev cannot currently query. If your collaboration policy requires machine-verifiable controls only, omit attestations; the route will require explicit consent or block.

“No training” and “zero retention” are separate requirements. Encryption in transit, smaller/compressed prompts, or a provider being called “enterprise” do not by themselves satisfy either requirement.
