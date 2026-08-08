# Windows scripts

Windows PowerShell automation scripts belong here. Common prompts, skills, and provider profiles remain at the repository root; do not duplicate them under this directory.

## Trusted issue-to-PR workflow

`scripts/run-real-issue.ps1` delegates to `windows/scripts/issue-to-pr-cycle.ps1`, which preserves the existing prepare, branch, planning, implementation, local-check, repair, PR/CI, verification, and issue-status stages.

Provider resolution and model invocation are delegated to the shared Python modules. The PowerShell orchestrator does not implement Groq, OpenRouter, Ollama, Codex, Chat Completions, or Responses semantics.

## Provider profile

Set API keys in the current environment without committing them:

```powershell
$env:GROQ_API_KEY = "..."
$env:OPENROUTER_API_KEY = "..."
```

Copy `examples/providers/groq-openrouter-free.json` and replace `REPLACE_WITH_OPENROUTER_MODEL:free` with a currently accessible OpenRouter model that still ends in `:free`.

Run the non-mutating preflight:

```powershell
.\scripts\run-real-issue.ps1 `
  -WorkingDirectory C:\src\target-repo `
  -Mode Preflight `
  -ProviderProfile C:\src\AutoDev\examples\providers\groq-openrouter-free.json
```

Run one issue through the existing workflow:

```powershell
.\scripts\run-real-issue.ps1 `
  -WorkingDirectory C:\src\target-repo `
  -Username owner `
  -Repo repository `
  -Issue 46 `
  -ProviderProfile C:\src\AutoDev\examples\providers\groq-openrouter-free.json
```

The same profile independently configures `reader`, `synthesizer`, `planner`, `implementer`, `fixer`, and `verifier`. Model response text is parsed separately from `.autodev-run/current/model-invocations.json` telemetry.

## Other checked-in profiles

- `examples/providers/ollama-local-all-roles.json`: local Ollama for all roles.
- `examples/providers/ollama-cloud-nemotron-minimax.json`: opt-in Ollama Cloud mapping.
- `examples/providers/codex-command-profile.json`: Codex CLI profiles through the generic command transport.

The Codex Desktop application is optional. A complete run can execute headlessly through PowerShell and Python.

## Legacy compatibility

Existing `-PlannerProvider`, `-PlannerModel`, `-AgentProvider`, `-AgentModel`, `-PlannerAgentCommand`, and `-AgentCommand` parameters remain accepted. PowerShell forwards them to Python rather than validating provider names itself. Supplying only a legacy model still uses Ollama compatibility in Python.

When no provider profile or legacy provider/model override is supplied, the established direct-agent command workflow remains available and defaults to `codex exec`.

## Individual transition modes

`issue-to-pr-cycle.ps1` supports:

```text
Run
Plan
Prepare
Preflight
RenderImplementerPrompt
LocalCheck
PrAndCi
RenderVerificationRepair
ReadyForReview
Blocked
```

Use individual modes only for debugging or resuming the trusted workflow. Existing `codex-*` script names remain for compatibility.

See `docs/model-roles.md` for the provider schema, safe headers, request options, explicit output limits, OpenRouter free-only rules, failure classification, and telemetry fields.
