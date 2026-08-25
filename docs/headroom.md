# Optional Headroom compression

Headroom is an optional context-compression layer for AutoDev provider paths that explicitly integrate it. It is not required for AutoDev and it does not own workflow sequencing, OpenCode model routing, privacy policy, or durable state.

## Current runtime boundary

OpenCode is AutoDev's default role runtime. OpenCode owns the provider transport for `opencode run --agent autodev-*` role processes, so AutoDev's provider-layer Headroom proxy is **not** automatically in that transport path.

Do not treat `headroom wrap opencode` as a required or canonical AutoDev launch command. Run AutoDev normally:

```text
autodev issue-to-pr 123
```

or through the installed OpenCode frontend:

```text
/autodev-issue-to-pr 123
```

For OpenCode roles, AutoDev still reduces repeated context through its durable-artifact context-sizing contract. When a provider profile enables Headroom but the role is executed through direct OpenCode transport, sizing telemetry records that Headroom was configured but not applied to that role rather than claiming a compression saving that did not occur. See [`opencode-context-sizing.md`](opencode-context-sizing.md).

## Safety model

Where AutoDev does own a Headroom-enabled provider path, it compresses only prompt sections with known boundaries. Requirements, role/safety instructions, output contracts, and other protected control text stay intact. If a prompt shape is unknown, AutoDev leaves it uncompressed instead of guessing.

For supported prompt shapes AutoDev:

1. identifies compressible evidence ranges;
2. sends only those ranges to the configured local Headroom `/v1/compress` endpoint;
3. reassembles the prompt with protected text unchanged;
4. records safe compression telemetry without prompt contents or credentials.

The semantic verifier's issue/acceptance criteria and output contract remain protected. Synthesized handoff, plan, changed files, diff, deterministic evidence, cross-file evidence, and uncertainty notes are bounded evidence sections when the owning provider path supports compression.

## Configuration

A provider-profile JSON may contain a top-level `headroom` section:

```json
{
  "headroom": {
    "enabled": true,
    "proxy_url": "http://127.0.0.1:8787/v1",
    "mode": "lossless",
    "output_shaping": false,
    "fail_open": true,
    "roles": {
      "reader": { "enabled": true },
      "planner": { "enabled": true },
      "implementer": { "enabled": true },
      "fixer": { "enabled": true },
      "verifier": { "enabled": false }
    }
  }
}
```

`mode` must remain `lossless` and output shaping must remain disabled. Verifier compression defaults to disabled unless explicitly enabled for that role.

Provider-profile Headroom settings do not replace OpenCode's role/model mapping in `opencode.json` / `opencode.jsonc`.

## Privacy

Headroom never weakens the active privacy gate. Where AutoDev routes a provider request through the Headroom proxy, the same provider privacy controls are applied to the direct/fail-open path and the proxy path. Compression does not authorize a model route that privacy policy would otherwise block.

See [`privacy.md`](privacy.md).

## Failure behavior

Compression is fail-open only when configured that way. A compression-only failure may fall back to the original prompt. Provider authentication, rate-limit, model, privacy, or other upstream failures remain provider/runtime failures and are not silently rerouted to another model.

## Telemetry

AutoDev stores only safe compression/context-sizing metadata such as status, mode, section count, timing, hashes, provider-reported compression metrics, and whether Headroom was actually applied to the relevant route. Prompt contents and credential values are not written to telemetry.
