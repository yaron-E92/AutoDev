# OpenCode role-model mapping examples

These files are ready-to-copy examples for the four scenarios documented in `docs/opencode.md`.

| Scenario | Example |
| --- | --- |
| One model for every AutoDev role | `one-model-all-roles/opencode.jsonc` |
| Mixed Groq/OpenRouter roles | `mixed-groq-openrouter/opencode.jsonc` |
| All-local Ollama | `all-local-ollama/opencode.jsonc` |
| Lightweight coordinator + stronger implementer/fixer | `cheap-coordinator-strong-implementer/opencode.jsonc` |

Copy the desired file to the target repository as `opencode.jsonc`, then replace every `<...>` placeholder with a real OpenCode `provider/model-id`.

Do not put provider API keys in these files. Credentials remain in normal OpenCode/provider/user environment configuration.

The AutoDev installer does not copy these examples into target repositories and does not modify an existing `opencode.json` or `opencode.jsonc`.
