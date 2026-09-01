# OpenCode integration

OpenCode is AutoDev's default model-role runtime and an optional command frontend. Python remains the owner of deterministic workflow sequencing, durable state, verification, repair budgets, PR/CI progression, and resume.

## Install into a target repository

```text
autodev repo install
```

This installs the maintained `.opencode/commands/` and `.opencode/agents/` assets. Root `opencode.json` / `opencode.jsonc` remain user-owned. Explicit AutoDev agent models in those files have precedence, while machine/user-local AutoDev model profiles can fill roles that would otherwise inherit.

Dedicated scheduler workers do not require those maintained runtime assets to be committed merely for scheduler execution. `autodev scheduler install` provisions worker-owned copies when the clone lacks repository-tracked versions, preserves tracked repository-owned assets, and verifies discoverability with `opencode agent list` before registering the scheduler. See [`scheduler.md`](scheduler.md).

## Run an issue

Inside OpenCode:

```text
/autodev-issue-to-pr 123
```

Equivalent canonical CLI:

```text
autodev issue-to-pr 123
```

`autodev coordinate --arguments 123` is the supported advanced/integration spelling over the same coordinator; it is not the normal user-facing workflow.

Resume/status:

```text
/autodev-status
/autodev-resume
```

or:

```text
autodev status
autodev resume
```

AutoDev never merges the resulting PR automatically.

## Agent model mapping

OpenCode exposes seven AutoDev agent mappings: the coordinator frontend plus the six workflow roles. Configure any of them through normal OpenCode configuration:

```json
{
  "agent": {
    "autodev-coordinator": { "model": "provider/coordinator-model" },
    "autodev-reader": { "model": "provider/reader-model" },
    "autodev-synthesizer": { "model": "provider/synthesizer-model" },
    "autodev-planner": { "model": "provider/planner-model" },
    "autodev-implementer": { "model": "provider/implementer-model" },
    "autodev-fixer": { "model": "provider/fixer-model" },
    "autodev-verifier": { "model": "provider/verifier-model" }
  }
}
```

Mappings may be configured independently. For the six workflow roles, a selected AutoDev user model profile can fill unmapped/inherited roles without creating a repository file. The OpenCode-only coordinator remains configurable through OpenCode configuration. Unmapped roles otherwise inherit according to the OpenCode/AutoDev resolution contract. The coordinator's model does not make it the owner of workflow state: Python still decides deterministic transitions and validates durable role acceptance before advancing.

AutoDev rejects unsupported ad-hoc per-run model overrides. Use `autodev config ...` for reusable machine/user-local profiles, and `/autodev-models` when installed or `autodev models` from the shell to inspect the effective safe mapping and source. See [`configuration.md`](configuration.md) for profile precedence and examples.

## Role boundaries

Python prepares bounded workflow-role input, launches the selected runtime/agent, validates the role output/acceptance record, and only then advances. A zero exit code without a valid accepted artifact is not success.

Standalone OpenCode role commands remain available for deliberate intervention/debugging where a public frontend exists:

```text
/autodev-read 123
/autodev-plan 123
/autodev-implement 123
/autodev-fix 123
/autodev-verify 123
```

Synthesis is part of the coordinated workflow and does not have a separate public OpenCode command. The standalone role commands above are not substitutes for the normal `issue-to-pr` coordinator when a complete autonomous run is desired.

## Privacy

Provider/runtime authorization happens before model work. API keys remain in the normal provider/OpenCode/user environment; do not place credentials in AutoDev command/agent files or repository documentation.

Persistent privacy grants are explicit, scoped, revocable, and user-local. Headless runs can consume an existing valid grant but cannot create one. See [`privacy.md`](privacy.md).

## Prompt policy and Headroom

AutoDev applies its role-specific prompt policy while preparing workflow-role context. Headroom is optional and may compress only known evidence ranges on provider paths where AutoDev actually owns that transport. Neither mechanism changes OpenCode's agent/model mapping.

See [`model-roles.md`](model-roles.md) and [`headroom.md`](headroom.md).

## Resume and failure handling

Durable state lives under `.autodev-run/current`. Restarting OpenCode does not require replaying completed work; `/autodev-resume` delegates to Python's checkpoint/resume engine and fails closed on source/artifact/fingerprint drift.

See [`opencode-resume.md`](opencode-resume.md) and [`opencode-runtime-hardening.md`](opencode-runtime-hardening.md).
