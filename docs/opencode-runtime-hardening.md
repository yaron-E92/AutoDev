# OpenCode runtime hardening

The installed OpenCode bridge runs through `automation.opencode_runtime` before entering the shared `automation.opencode_adapter` frontend. The runtime layer exists only for OpenCode-specific reliability boundaries; shared provider-backed AutoDev workflows continue to use the shared stage implementation directly.

## User-owned OpenCode project config

Root-level `opencode.json` and `opencode.jsonc` are supported user-owned OpenCode configuration. During an OpenCode AutoDev run they are excluded from source-workspace drift and snapshot checks, because model/provider routing is already captured through OpenCode role-model fingerprints rather than treated as product source.

This exception is exact and root-only. Files such as `src/opencode.jsonc`, `opencode.jsonc.backup`, or arbitrary modified/untracked source remain normal source changes and still make a prepared-base verification fail when appropriate.

For unrelated machine-local files that AutoDev does not explicitly support, prefer Git's checkout-local exclude file instead of weakening repository-wide ignore rules:

```text
.git/info/exclude
```

For example, a developer may place editor scratch files or machine-only notes there without committing a `.gitignore` change for every contributor. This is no longer required for the supported root `opencode.json` / `opencode.jsonc` files.

## Exact launcher and repository-relative bridge paths

`.opencode/autodev.json` owns the installed Python launcher. Coordinator and role contracts must use that launcher exactly; they must not probe `python` versus `python3`, invent absolute interpreter/repository paths, use `cd`/`bash -c` wrappers, or search for alternate bridge copies in temporary directories.

The active repository's own `.opencode` directory is installer-owned workflow state. AutoDev intentionally keeps bridge references repository-relative so a model does not turn a repo-local path into an accidental external-directory request.

## Durable role acceptance

An OpenCode Task returning, printing success, or receiving a green UI checkmark is not workflow proof. After every delegated AutoDev role, the coordinator runs the installed `role-check --role <role>` bridge operation.

The role check reads `state.json`'s accepted-role record and, for file-backed outputs, re-hashes the accepted artifact. It returns one of:

```text
ACCEPTED
MISSING
STALE
```

Only `ACCEPTED` permits a dependent role or stage to start. This prevents provider/session/compaction failures from being rendered as workflow progress when no role artifact was actually accepted.

Role-check diagnostics are bounded: they report the selected provider/model, model-mapping source, expected output, and byte sizes/existence for the role's bounded input/template artifacts. They do not dump prompts, role transcripts, or secrets.

## Headroom diagnostics

Headroom remains optional. When the OpenCode process indicates a Headroom-wrapped configuration, role-check diagnostics probe the loopback Headroom health endpoint with a short timeout and report whether the proxy is reachable/ready, whether the selected role provider is routed through the `headroom` provider or bypasses it, and any exposed Kompress readiness/status.

A Headroom failure or bypass is diagnostic evidence, not permission to silently change provider/model routing. AutoDev does not make Headroom mandatory and does not classify an upstream proxy/provider failure as a code-repairable implementation bug.

## Terminal failure preservation

The hardened runtime persists the precise payload from a failed OpenCode stage before the coordinator enters the terminal `failed` boundary. The terminal payload preserves the originating issue number, branch/completed stage when available, actual failed stage, classification, reason, and failure fingerprint instead of replacing them with a generic `failed` / `OpenCode coordinator failed` record.

A later successful stage clears stale persisted frontend failure context so an unrelated future failure cannot inherit an obsolete reason.

## Resume authority

`/autodev-resume` still delegates continuation decisions to the existing Python resume engine. Its returned `next_action`, `next_role`, `next_stage`, and repair counters are authoritative. If the exact resume bridge invocation cannot run or cannot return authoritative JSON, the coordinator must fail instead of reconstructing the workflow from chat history or manifest prose.

This separation is intentional:

```text
Python owns durable workflow state and boundaries.
OpenCode Tasks own model-heavy role work.
The coordinator may order those operations, but it may not invent state.
```

## Upstream failures

AutoDev does not attempt to fix failures inside OpenCode, Ollama, Groq, Headroom, or a model implementation. In particular, a model may still terminate without a tool call and OpenCode/provider session compaction may still fail before the role can produce an artifact.

The AutoDev-side guarantee is fail-closed: those failures cannot become accepted workflow progress. The missing/stale role boundary and bounded provider/model/input diagnostics explain where the run stopped without silently substituting another model or exposing role transcripts.
