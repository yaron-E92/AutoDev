# OpenCode integration

OpenCode is AutoDev's default model-role runtime and an optional command frontend. Python remains the owner of deterministic workflow sequencing, durable state, verification, repair budgets, PR/CI progression, and resume.

## Install into a target repository

```text
autodev repo install
```

This installs the maintained `.opencode/commands/` and `.opencode/agents/` assets. Root `opencode.json` / `opencode.jsonc` remain user-owned and are the authority for OpenCode model mapping.

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

## Role model mapping

Configure models through normal OpenCode configuration:

```json
{
  "agent": {
    "autodev-reader": { "model": "provider/reader-model" },
    "autodev-planner": { "model": "provider/planner-model" },
    "autodev-implementer": { "model": "provider/implementer-model" },
    "autodev-verifier": { "model": "provider/verifier-model" }
  }
}
```

Roles may be mapped independently. Unmapped roles inherit normal OpenCode configuration. AutoDev rejects unsupported ad-hoc per-run model overrides instead of creating a competing model-routing layer.

Use `/autodev-models` when installed or `autodev models` from the shell to inspect the effective safe role/model mapping.

## Role boundaries

Python prepares bounded role input, launches the selected runtime/agent, validates the role output/acceptance record, and only then advances. A zero exit code without a valid accepted artifact is not success.

Standalone OpenCode role commands remain available for deliberate intervention/debugging:

```text
/autodev-read 123
/autodev-plan 123
/autodev-implement 123
/autodev-fix 123
/autodev-verify 123
```

They are not substitutes for the normal `issue-to-pr` coordinator when a complete autonomous run is desired.

## Privacy

Provider/runtime authorization happens before model work. API keys remain in the normal provider/OpenCode/user environment; do not place credentials in AutoDev command/agent files or repository documentation.

Persistent privacy grants are explicit, scoped, revocable, and user-local. Headless runs can consume an existing valid grant but cannot create one. See [`privacy.md`](privacy.md).

## Prompt policy and Headroom

AutoDev applies its role-specific prompt policy while preparing role context. Headroom is optional and may compress only known evidence ranges. Neither mechanism changes OpenCode's model mapping.

See [`model-roles.md`](model-roles.md) and [`headroom.md`](headroom.md).

## Resume and failure handling

Durable state lives under `.autodev-run/current`. Restarting OpenCode does not require replaying completed work; `/autodev-resume` delegates to Python's checkpoint/resume engine and fails closed on source/artifact/fingerprint drift.

See [`opencode-resume.md`](opencode-resume.md) and [`opencode-runtime-hardening.md`](opencode-runtime-hardening.md).
