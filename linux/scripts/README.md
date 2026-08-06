# Linux scripts

Linux-specific automation scripts belong here. Common prompts, skills, and provider profiles remain at the repository root.

The Linux workflow consists of:

- `linux/run-once.sh` for timer-friendly execution.
- `linux/scripts/issue-to-pr-cycle.sh` for the complete trusted workflow.
- `linux/scripts/*.sh` for deterministic prepare, finalize, mark, and environment primitives.
- `linux/config.example.env` for sanitized project configuration.
- `linux/systemd/` for optional service and timer templates.

## Provider profile workflow

The shell script owns workflow stages only. Provider validation, role resolution, HTTP/command invocation, policy composition, output parsing, failure classification, and telemetry are delegated to Python.

Set credentials in the environment file or process environment without committing their values:

```bash
GROQ_API_KEY=...
OPENROUTER_API_KEY=...
PROVIDER_PROFILE=/path/to/AutoDev/examples/providers/groq-openrouter-free.json
```

Copy the mixed profile and replace `REPLACE_WITH_OPENROUTER_MODEL:free` with a currently accessible OpenRouter model that still ends in `:free`.

Run the non-mutating preflight:

```bash
linux/scripts/issue-to-pr-cycle.sh \
  --env ~/automation/state/PROJECT.env \
  --mode Preflight \
  --provider-profile examples/providers/groq-openrouter-free.json
```

Run one complete issue-to-PR cycle:

```bash
linux/scripts/issue-to-pr-cycle.sh \
  --env ~/automation/state/PROJECT.env \
  --mode Run \
  --owner OWNER \
  --repo REPO \
  --base main \
  --remote origin \
  --issue 46 \
  --provider-profile examples/providers/groq-openrouter-free.json
```

Omit `--issue` to select the next eligible `autodev:ready` issue. `--description` and `--description-file` remain available for local task descriptions.

The profile independently configures `reader`, `synthesizer`, `planner`, `implementer`, `fixer`, and `verifier`. Linux preparation forwards the profile to the shared area-reader planner. Invocation telemetry is written separately to `.codex-run/current/model-invocations.json`.

## Checked-in profiles

- `examples/providers/groq-openrouter-free.json`: Groq reasoning roles plus an explicit OpenRouter `:free` implementation model.
- `examples/providers/ollama-local-all-roles.json`: local Ollama for all roles.
- `examples/providers/ollama-cloud-nemotron-minimax.json`: opt-in Ollama Cloud mapping.
- `examples/providers/codex-command-profile.json`: Codex CLI profiles through the command transport.

Codex Desktop is optional; the workflow can run headlessly through Bash and Python.

## Legacy compatibility

`--planner-provider`, `--planner-model`, `--agent-provider`, `--agent-model`, `--planner-agent-command`, and `--agent-command` remain accepted. Bash forwards them to Python instead of restricting provider names. Supplying only a legacy model continues to use Ollama compatibility in Python.

When no profile or legacy provider/model override is selected, the existing direct-agent command path remains available and defaults to `codex exec`.

## Individual modes

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

Use individual modes for debugging or resuming the deterministic workflow. `linux/run-once.sh` continues to resolve `issue-to-pr-cycle.sh` relative to its installation and can be used by the existing systemd timer.

See `docs/model-roles.md` for the profile schema, safe headers, output-limit behavior, free-only safeguards, provider-neutral preflight, telemetry fields, and failure classifications.
