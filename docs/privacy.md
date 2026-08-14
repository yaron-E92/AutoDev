# Model data privacy

AutoDev can enforce a repository-level policy before model-heavy repository content is sent to cloud LLMs.

The decision order is intentionally fail-closed:

```text
resolve effective route
  -> apply machine-enforceable privacy controls
  -> verify those controls are effective
       -> verified: ALLOW
       -> not enforceable/verifiable: require explicit consent
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
| `local-only` | only by explicit exception | denied | zero |
| `off` | yes | not checked | not checked |

`AUTODEV_PRIVACY_PROFILE` can strengthen a repository policy for a run, but it cannot weaken it. For example, a repository declaring `strict-confidential` cannot be changed to `off` merely by setting the environment variable.

Scratch directories that are not Git repositories default to `off` so fixtures/tests do not accidentally acquire cloud-policy behavior. Production repositories default to strict handling.

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

This follows OpenRouter's documented provider-routing controls. `data_collection: "deny"` excludes endpoints that collect customer data, and `zdr: true` restricts routing to Zero Data Retention endpoints.

When Headroom is in the AutoDev provider path, the same controls are applied to both the direct/fail-open provider and the Headroom proxy provider. Compression must not remove or weaken privacy routing.

### OpenRouter through OpenCode

OpenCode model calls are made by isolated `opencode run --agent autodev-*` subprocesses. Before starting one of those subprocesses AutoDev:

1. resolves the role's effective `provider/model` mapping;
2. uses a runtime `OPENCODE_CONFIG_CONTENT` overlay to add the OpenRouter privacy fields;
3. runs `opencode debug config` with that overlay;
4. checks the resolved effective configuration contains the required controls;
5. only then launches the role process using that verified environment.

The overlay is runtime-scoped and does not rewrite a developer's global OpenCode configuration. It supports the current OpenCode provider/body configuration as well as the legacy OpenRouter model-options form used by older OpenCode configuration.

If a higher-precedence/managed configuration removes or overrides the requested privacy values, verification fails and AutoDev does not treat the route as safe.

## Provider policies that cannot be switched per request

Some cloud providers expose data controls at account/project level rather than as a request field AutoDev can safely toggle and verify.

AutoDev keeps reviewed provider-policy metadata with a freshness limit. Current classifications include:

- Groq inference: not used for training, but customer content may be temporarily retained for reliability/abuse monitoring unless account ZDR is enabled.
- OpenAI API: API data is not used for training by default, but customer content can appear in abuse-monitoring logs for up to 30 days unless an eligible organization/project has stronger retention controls such as ZDR.
- Ollama Cloud: prompt/response content is processed transiently and not used for training; Ollama states it is not stored beyond request processing.
- local inference: customer content remains local for purposes of this gate.

Because AutoDev cannot currently prove a Groq/OpenAI account-level ZDR setting from the ordinary inference request, `strict-confidential` does not automatically claim those direct routes are zero-retention. It requires a verified future adapter/control or explicit consent. The less strict `no-training` profile may allow them while the reviewed no-training policy remains fresh.

Provider-policy metadata is deliberately time-bounded. Once stale, it becomes unknown and strict runs fail closed/require consent instead of trusting an old promise forever.

## Explicit consent

If enforcement/verification cannot meet the active policy, AutoDev asks for consent when it has an interactive input channel. The prompt identifies the role, route, known training/retention status, and the unmet requirement. The default answer is No.

Non-interactive/headless execution cannot manufacture consent. It blocks before the prompt is sent.

For a deliberately pre-authorized exception in a non-interactive run, use an exact role+route entry:

```powershell
$env:AUTODEV_PRIVACY_CONSENT = "implementer=groq/groq/model-id"
```

Multiple exact entries may be comma-separated. This is intentionally narrow: authorizing one role/route does not authorize another role or a later provider/model change.

Prefer fixing/enabling a provider's privacy controls rather than relying on consent exceptions.

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
controls applied
policy source/review date
consent scope, if any
```

They do not contain API keys, Authorization headers, prompts, repository source, or hidden reasoning.

## Direct/headless target repository resolution

The privacy layer uses the explicit target repository when supplied by the caller. For CLI workflows it also supports `AUTODEV_TARGET_REPO`; callers that orchestrate another checkout should set that variable to the target repository before making model calls.

## Important limits

Passing this gate is a technical AutoDev data-handling decision, not a legal-compliance certification. It does not claim GDPR, contractual, export-control, or other legal compliance.

The gate covers model calls made by AutoDev and the isolated OpenCode role subprocesses AutoDev launches. It cannot govern unrelated tools a developer manually invokes outside AutoDev.

“No training” and “zero retention” are separate requirements. Encryption in transit, smaller/compressed prompts, or a provider being called “enterprise” do not by themselves satisfy either requirement.
