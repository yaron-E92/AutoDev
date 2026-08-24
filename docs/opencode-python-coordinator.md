# Deterministic OpenCode coordinator

AutoDev's OpenCode frontend uses the same Python-owned workflow as the first-class `autodev` CLI. OpenCode runs isolated role agents; Python owns stage sequencing, durable state, verification, repair counters, resume selection, PR/CI progression, and terminal outcomes.

## Install

From the target repository:

```text
autodev repo install
```

This installs the maintained `.opencode/commands/` and `.opencode/agents/` assets. Model routing remains in `opencode.json` / `opencode.jsonc`.

## Run

Inside OpenCode:

```text
/autodev-issue-to-pr 123
```

Resume with:

```text
/autodev-resume
```

The installed commands invoke the first-class AutoDev launcher rather than a repository-local Python bridge.

## Role execution

For model-backed work Python selects the role, prepares its bounded durable input, launches the configured runtime/agent, validates the output contract, records accepted-artifact identity, and only then advances.

A successful child-process exit without a valid accepted artifact fails closed. Protocol correction is bounded and remains part of the deterministic coordinator contract.

Standalone `/autodev-read`, `/autodev-plan`, `/autodev-implement`, `/autodev-fix`, and `/autodev-verify` commands remain available for intentional role-level debugging/intervention.

AutoDev never merges the resulting pull request automatically.
