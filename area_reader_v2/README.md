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
