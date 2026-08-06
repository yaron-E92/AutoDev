# Area-reader v2 command groups

Area-reader v2 keeps command discovery separate from recommendation selection:

- `available_command_groups` lists every generated group supported by `verification-command-groups.json` and `verification-commands.sh`.
- `recommended_command_groups` is the safe default local verification set for the current issue scope.
- `conditional_command_groups` documents groups that remain available but should be run only when issue text, changed paths, or environment facts make them relevant.

For generic local verification and issue-to-PR readiness work, the recommended groups are:

```json
[
  "env",
  "dotnet-solution",
  "node-root",
  "markdown-smoke"
]
```

`api-client-generate`, `web-app`, `maui-android-doctor`, and `maui-android-build` are conditional. MAUI Android groups are not default recommendations for non-mobile issues, and `maui-android-build` also requires Android SDK availability. `ci-manual-reference` is reference-only and is not part of default local verification recommendations.

## Provider roles

`area_reader_v2.runner` accepts the same version-2 `--provider-config` profile used by the operational runner. It resolves and invokes:

```text
repository inspection  -> reader
cross-area synthesis   -> synthesizer
implementation plan    -> planner
```

Provider transport, model, headers, request options, prompt policy, timeout, safe invocation metadata, and response telemetry are resolved in Python. HTTP response text is passed to the existing area-reader parsers while usage, cost, timing, and sanitized failures remain in `model-invocations.json`.

Linux preparation forwards `--provider-profile` through `automation.prepare_planner_prompt`, so area reading and later planner/implementer stages use one consistent profile. Existing reader/coder and synthesizer compatibility flags remain supported.

See `docs/model-roles.md` and `examples/providers/` for provider-neutral profiles and configuration details.
