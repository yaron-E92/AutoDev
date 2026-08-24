# OpenCode runtime hardening

OpenCode is AutoDev's default role runtime. Python owns deterministic workflow state; OpenCode owns model execution for installed `autodev-*` agents.

## User-owned OpenCode configuration

Root `opencode.json` and `opencode.jsonc` are supported user-owned runtime/model configuration. During OpenCode execution those exact root files are excluded from product-source drift checks because their effective model identity is captured separately in role fingerprints.

This exclusion is narrow. Similar files elsewhere in the repository remain normal source changes.

## First-class launcher

Installed OpenCode commands invoke the first-class `autodev` launcher. Do not reintroduce repository-local Python bridge launchers, bridge configuration files, interpreter probing, alternate bridge copies, or shell wrappers around the canonical command.

## Durable role acceptance

A child process exiting successfully is not workflow proof. Python validates the role's acceptance record and, for file-backed outputs, the accepted artifact hash before dependent work can advance.

Role diagnostics are bounded to safe runtime/model identity and artifact state. They do not dump prompts, hidden reasoning, credentials, or unbounded transcripts.

## Headroom diagnostics

Headroom remains optional. When expected, AutoDev may report bounded health/routing diagnostics, but a Headroom problem is not permission to change provider/model routing and is not treated as a code-repairable repository defect.

## Terminal failure preservation

Runtime failures retain their originating stage, classification, reason, and bounded fingerprint. Later success clears stale transient failure context so an unrelated failure cannot inherit old diagnostics.

## Resume authority

`/autodev-resume` and `autodev resume` delegate continuation decisions to the Python resume engine. Returned next role/stage and repair counters are authoritative. AutoDev fails closed if it cannot derive an authoritative next boundary from durable state.

```text
Python owns workflow state and boundaries.
OpenCode owns model-heavy role execution.
No chat process may invent durable progress.
```
