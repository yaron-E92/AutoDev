# OpenCode role-model mapping examples

These ready-to-copy examples demonstrate several supported ways to map AutoDev's OpenCode roles to provider models. See [`../../docs/opencode.md`](../../docs/opencode.md) and [`../../docs/model-roles.md`](../../docs/model-roles.md) for the current runtime and role contracts.

| Scenario | Example |
| --- | --- |
| One model for every AutoDev role | `one-model-all-roles/opencode.jsonc` |
| Mixed Groq/OpenRouter roles | `mixed-groq-openrouter/opencode.jsonc` |
| All-local Ollama | `all-local-ollama/opencode.jsonc` |
| Lightweight coordinator + stronger implementer/fixer | `cheap-coordinator-strong-implementer/opencode.jsonc` |

Copy the desired file to the target repository as `opencode.jsonc` only when that repository intentionally owns the mapping, then replace every `<...>` placeholder with a real OpenCode `provider/model-id`. If model routing is user-local instead, keep the equivalent mapping in normal user-owned OpenCode configuration rather than committing a repository copy.

Do not put provider API keys in these files. Credentials remain in normal OpenCode/provider/user environment or secret-store configuration.

`autodev repo install` does not copy these examples into target repositories and does not rewrite an existing `opencode.json` or `opencode.jsonc`. Inspect the effective mapping with:

```text
autodev models
```
