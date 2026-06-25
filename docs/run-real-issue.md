# AutoDev Real-Issue Runner

`automation/run_real_issue.py` is the AutoDev orchestrator for running real GitHub issues through planning, model-proposed patches, deterministic verification, optional fix attempts, and optional draft PR creation.

The runner is provider-agnostic. A provider is only a transport for sending a prompt and receiving text. The canonical HTTP provider name is `chat-completions` because it targets the OpenAI-compatible `/v1/chat/completions` protocol used by LM Studio, Ollama OpenAI-compatible mode, llama.cpp server, vLLM, OpenRouter, and similar servers. `openai-compatible` is accepted as a backwards-compatible alias.

## Labels

AutoDev uses:

```text
autodev:ready
autodev:running
autodev:blocked
autodev:failed
autodev:done
```

Use the existing label helper scripts to create or update labels:

```bash
linux/scripts/ensure-labels.sh --owner owner --repo AutoDev
```

```powershell
windows\scripts\ensure-codex-labels.ps1 -Username owner -Repo AutoDev
```

## Specific Issue

```bash
scripts/run-real-issue.sh \
  --repo . \
  --github-repo owner/AutoDev \
  --issue 18 \
  --mode implement \
  --out .autodev-runs/issue-18 \
  --reader-provider command \
  --reader-command "ollama run qwen35-9b-32k" \
  --coder-provider command \
  --coder-command "ollama run devstral-small2-12k"
```

## Next Issue

```bash
scripts/run-real-issue.sh \
  --repo . \
  --github-repo owner/AutoDev \
  --next \
  --selection oldest \
  --manage-labels \
  --mode pr \
  --out .autodev-runs/next
```

`--next` lists open issues with `autodev:ready`, excludes `autodev:running` and `autodev:blocked`, and selects the oldest issue by default. Use `--selection newest` to reverse that.

With `--manage-labels`, the runner adds `autodev:running` before implementation, removes it on completion, adds `autodev:done` after draft PR creation, and adds `autodev:failed` on failure. It never closes issues directly and does not remove `autodev:ready` by default.

## Providers

Reader and coder providers are configured separately:

```text
--reader-provider command|chat-completions|openai-compatible|mock
--reader-command <command>
--reader-base-url <url>
--reader-model <model-name>
--reader-api-key-env <ENV_VAR_NAME>
--reader-timeout-seconds <number>

--coder-provider command|chat-completions|openai-compatible|mock
--coder-command <command>
--coder-base-url <url>
--coder-model <model-name>
--coder-api-key-env <ENV_VAR_NAME>
--coder-timeout-seconds <number>
```

Defaults:

```text
reader provider: command
coder provider: command
reader model: qwen35-9b-32k
coder model: devstral-small2-12k
```

For `command`, provide a command. The prompt is passed on stdin and stdout is treated as the model response.

```bash
--reader-provider command --reader-command "ollama run qwen35-9b-32k"
--coder-provider command --coder-command "ollama run devstral-small2-12k"
```

For `chat-completions`, provide a base URL and model. Local servers can omit API keys.

```bash
--reader-provider chat-completions \
--reader-base-url http://localhost:1234/v1 \
--reader-model qwen35-9b-32k
```

Remote servers can use API keys through environment variables. The runner records the variable name, not the secret.

```bash
--coder-provider chat-completions \
--coder-base-url https://api.example.com/v1 \
--coder-model devstral-small2-12k \
--coder-api-key-env AUTODEV_CODER_API_KEY
```

Ollama can be used either through `command` or through its OpenAI-compatible endpoint:

```bash
--reader-provider chat-completions \
--reader-base-url http://localhost:11434/v1 \
--reader-model qwen35-9b-32k
```

## Provider Config File

```json
{
  "reader": {
    "provider": "chat-completions",
    "base_url": "http://localhost:1234/v1",
    "model": "qwen35-9b-32k"
  },
  "coder": {
    "provider": "command",
    "command": "my-coder-cli --model devstral-small2-12k",
    "model": "devstral-small2-12k"
  }
}
```

Pass it with:

```bash
--provider-config autodev-providers.json
```

CLI arguments override config file values.

## Modes

- `plan-only`: fetches the issue, runs area-reader planning, writes outputs, and does not call the coder. Verification only runs with `--baseline-verify`.
- `implement`: plans, calls the coder, extracts a patch, applies it, verifies, and runs fixer attempts when verification fails.
- `pr`: same as `implement`, then commits issue-scoped changes, pushes the branch, and opens a draft PR.
- `--skip-implementation`: preserves the old manual behavior by writing `implementation-prompt.md` without calling the coder.
- `--dry-run-implementation`: calls the coder and saves the raw response and extracted patch without applying it, verifying, or creating a PR.

## Patch Contract

The coder must output one of:

```text
BEGIN_UNIFIED_DIFF
<unified git diff>
END_UNIFIED_DIFF
```

or:

```text
NO_CHANGES_REQUIRED
<short explanation>
```

The model never runs shell commands. AutoDev applies patches with `git apply --index` first, then falls back to `git apply` if the index-aware application is too strict.

## Verification

Area-reader emits command group JSON. The real-issue runner executes recommended command groups directly from Python so Linux and Windows follow the same orchestration path. If a command group requires a missing executable such as Bash, the command fails clearly and the fixer loop gets the captured logs.

Verification files:

```text
verification/attempt-0.md
verification/attempt-1.md
verification/attempt-2.md
verification-result-summary.md
```

`verification-result-summary.md` is written on both success and failure.

## Outputs

Concise outputs are written by default:

```text
issue.md
selected-issue.json
routed-areas.json
synthesized-handoff.md
coder-plan.md
recommended-command-groups.json
implementation-prompt.md
model-responses/
model-patches/
verification/
verification-result-summary.md
final-pr-summary.md
provider-metadata.json
```

Pass `--debug-artifacts` to keep raw benchmark-style area-reader artifacts.

## Safety

The runner refuses dirty worktrees unless `--allow-dirty` is passed, creates new branches with `autodev/issue-<number>-<slug>`, refuses to create PRs from `main` or `master`, refuses to commit run artifacts from `--out`, never auto-merges, never approves its own PR, and never manually triggers remote CI.

## Wrappers

Linux:

```bash
scripts/run-real-issue.sh --help
```

Windows PowerShell:

```powershell
scripts\run-real-issue.ps1 --help
```

The Windows wrapper does not require WSL.
