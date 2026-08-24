# Optional Headroom compression

Headroom is an optional context-compression layer. It is not required for AutoDev and it does not own workflow sequencing, model routing, privacy policy, or durable state.

## Safety model

AutoDev compresses only prompt sections with known boundaries. Requirements, role/safety instructions, output contracts, and other protected control text stay intact. If a prompt shape is unknown, AutoDev leaves it uncompressed instead of guessing.

For supported prompt shapes AutoDev:

1. identifies compressible evidence ranges;
2. sends only those ranges to the configured local Headroom `/v1/compress` endpoint;
3. reassembles the prompt with protected text unchanged;
4. records safe compression telemetry without prompt contents or credentials.

The canonical semantic verifier uses `promptTemplates/semantic-verifier.md`; its synthesized handoff, plan, changed files, diff, deterministic evidence, cross-file evidence, and uncertainty notes are independently bounded evidence sections, while the issue, acceptance criteria, and JSON output contract remain protected.

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

Provider-profile Headroom settings affect prompt preparation/compression metadata. OpenCode model routing remains owned by effective `opencode.json` / `opencode.jsonc` configuration.

## Running with OpenCode

Start Headroom separately when you intentionally use it, then launch OpenCode through the wrapper:

```text
headroom wrap opencode
```

Run AutoDev normally inside OpenCode:

```text
/autodev-issue-to-pr 123
```

Or use the first-class CLI outside the OpenCode command frontend:

```text
autodev coordinate --arguments 123
```

Headroom must never become a requirement for ordinary AutoDev execution.

## Failure behavior

Compression is fail-open only when configured that way: a compression-only failure may fall back to the original prompt. Provider/authentication/rate-limit/model failures remain provider failures and are not reclassified as compression failures or silently rerouted to another model.

## Telemetry

AutoDev stores only safe compression metadata such as status, mode, section count, timing, hashes, and provider-reported compression metrics. Prompt contents and credential values are not written to telemetry.
