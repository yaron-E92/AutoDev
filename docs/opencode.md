# OpenCode frontend

AutoDev's OpenCode integration is an optional role frontend. It does not replace the existing PowerShell or Python workflow engines.

OpenCode owns only the isolated model conversation for a selected role. AutoDev continues to own issue preparation, durable `.codex-run/current` artifacts, branch and issue state, deterministic verification, semantic-verification contracts, repair gates, commits, CI, pull requests, and resumability.

## Install into a target repository

From an AutoDev checkout on Windows:

```powershell
pwsh -File .\scripts\install-opencode.ps1 `
  -TargetRepository C:\src\TARGET_REPOSITORY
```

The installer creates or refreshes only these AutoDev-owned target files:

```text
.opencode/
  autodev.json
  autodev.ps1
  commands/
    autodev-read.md
    autodev-plan.md
    autodev-implement.md
    autodev-fix.md
    autodev-verify.md
  agents/
    autodev-reader.md
    autodev-synthesizer.md
    autodev-planner.md
    autodev-implementer.md
    autodev-fixer.md
    autodev-verifier.md
```

Running the installer again updates those named files and leaves unrelated `.opencode` commands, agents, and configuration untouched.

`autodev.json` contains only the local AutoDev checkout path and Python command. It contains no API keys. Because the AutoDev checkout path is machine-specific, keep `autodev.json` local unless all contributors intentionally use the same path. The command/agent Markdown files are safe to commit if a target repository wants the AutoDev commands to be discoverable for everyone.

To sync after updating AutoDev, rerun the same installer command.

## Prerequisites

The target repository must already be usable by the existing AutoDev Windows workflow. In particular, the existing AutoDev PowerShell setup, `pwsh`, Python, GitHub CLI authentication, and the normal `codex-tools` prerequisites used by `Prepare` must be available.

The bridge deliberately delegates initial issue setup to AutoDev's existing `Prepare` stage rather than reimplementing issue selection, labels, state, or branch rules.

## Commands

Launch OpenCode from the target repository and use:

```text
/autodev-read 49
/autodev-plan 49
/autodev-implement 49
/autodev-fix 49
/autodev-verify 49
```

All five commands use `subtask: true`, so each role runs in an isolated OpenCode context rather than accumulating reader/planner/implementer/fixer/verifier history in the primary conversation.

The synthesizer is installed as `autodev-synthesizer` for later coordinator use but intentionally has no prominent slash command in this first implementation.

This integration does not add `/autodev-issue-to-pr`, `/autodev-status`, or `/autodev-resume`. The existing AutoDev workflow and #37 manifest remain authoritative for orchestration and resume/status semantics.

## What the bridge does

Every public command follows the same thin pattern:

```text
OpenCode command
  -> .opencode/autodev.ps1
  -> automation.opencode_adapter prepare
  -> generated canonical AutoDev role prompt
  -> isolated OpenCode role agent
  -> normal .codex-run/current artifact / source edits
  -> automation.opencode_adapter accept when validation is needed
```

The adapter itself never invokes a configured AutoDev model provider.

If the requested issue is not already the current AutoDev issue, `prepare` delegates to the existing Windows `Prepare` stage. Provider/model environment overrides are removed for that call so issue preparation does not accidentally start a second model invocation.

The role prompt is then rendered from existing AutoDev code/templates and passed through the existing role-specific prompt-policy layer:

- reader: existing area-reader discovery and reader prompt builder with a bounded repository bundle;
- synthesizer: existing area-reader synthesis prompt builder;
- planner: existing role-aware planner prompt builder;
- implementer: `promptTemplates/implementer.md`;
- fixer: the current `verification-repair.md`, `local-repair.md`, or `ci-repair.md` produced by AutoDev;
- verifier: existing semantic-verifier evidence builder plus `promptTemplates/semantic-verifier.md`.

No canonical Planner/Implementer/Fixer/Verifier prompt body is copied into `.opencode` files.

## Durable artifacts

Role handoffs use the existing current run directory rather than chat history:

```text
.codex-run/current/issue.md
.codex-run/current/reader-brief.md
.codex-run/current/synthesized-handoff.md
.codex-run/current/plan.md
.codex-run/current/implementer.md
.codex-run/current/commit-message.txt
.codex-run/current/verification-result.json
.codex-run/current/verification/final-verdict.json
```

Reader/synthesizer results are limited to 30,000 characters before they can become a downstream handoff. The reader's deterministic repository bundle is separately capped before it is inserted into the generated reader prompt.

Planner output is validated through AutoDev's existing six-section planner parser. Semantic verifier JSON is validated through AutoDev's existing semantic schema and persisted as the normal semantic-attempt artifact. A standalone `repair` verdict is not written as `final-verdict.json`; finalization remains owned by the normal #35 repair/reverification gate. Standalone terminal `pass` or `blocked` verdicts may be persisted as final verdicts.

## Permissions

The installed role agents do not declare a model.

Provider and model selection therefore stays in the operator's normal OpenCode configuration or session selection. AutoDev does not hardcode Groq, OpenRouter, Ollama, or any specific free model into the integration.

Role permissions are intentionally narrow:

```text
reader       read/search + reader artifact only; task denied
synthesizer  bounded artifact read + synthesized handoff only; task denied
planner      read/search + plan artifact only; task denied
implementer  source edits; VCS/PR/issue mutation denied; task denied
fixer        targeted source edits; VCS/PR/issue mutation denied; task denied
verifier     read/search + verifier result; source edits denied; task denied
```

The role definitions preserve `.env` read protection. Implementer/fixer shell access remains ask-by-default, with explicit denials for branch/commit/push/PR/issue mutation and an explicit allow for the installed AutoDev bridge.

## Provider examples

OpenCode provider/model configuration is independent from AutoDev's provider-profile files. Select any OpenCode-supported provider/model normally, for example:

```text
reader/planner/verifier: Groq model selected in OpenCode
implementer/fixer: OpenRouter free model selected in OpenCode
```

or:

```text
all roles: local Ollama-backed models selected in OpenCode
```

The checked-in `.opencode` agents deliberately omit `model:` so changing OpenCode provider/model configuration does not require regenerating AutoDev prompts or editing workflow logic.

AutoDev's existing provider profiles remain available for the normal headless PowerShell/Python workflow and are not required for OpenCode role execution.

## Ponytail and Headroom

AutoDev's #34 prompt-policy layer is already applied when the bridge renders each role prompt. Normal OpenCode role execution therefore does not require an external Ponytail plugin. If an OpenCode Ponytail plugin is installed, configure or disable it for the `autodev-*` agents so it does not inject a second, potentially contradictory policy on top of AutoDev's rendered role policy.

Role isolation also does not require Headroom. Do not wrap OpenCode in a second Headroom/compression path for these commands. AutoDev's provider-side Headroom support remains owned by the normal provider-backed workflow; this frontend does not duplicate that routing or compression logic.

## Existing workflows remain independent

These entrypoints are unchanged and remain usable without OpenCode:

```text
scripts/run-real-issue.ps1
windows/scripts/issue-to-pr-cycle.ps1
automation.prompt_runner
automation.run_real_issue
```

OpenCode does not own branch creation, commits, pushes, pull requests, issue labels, CI loops, semantic repair semantics, or resumability. After an OpenCode role writes the expected durable artifact or source edits, continue with the existing AutoDev stage boundary appropriate for the run.
