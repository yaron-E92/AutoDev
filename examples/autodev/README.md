# AutoDev user-configuration examples

These files are examples for AutoDev's machine/user-local configuration described in [`../../docs/configuration.md`](../../docs/configuration.md). They are **not** target-repository `.autodev/` policy files.

- `config.default.json` is a minimal valid file equivalent to AutoDev's built-in defaults: OpenCode runtime, no selected AutoDev model profile, and a 15-minute scheduler cadence.
- `config.profiles.example.json` demonstrates named mixed/local/OpenRouter profiles, a user-wide active profile, and a per-repository override.

Find the configuration path on the current machine with:

```text
autodev config path
```

Prefer `autodev config ...` commands for normal edits because AutoDev validates and atomically writes the file. If copying an example manually, replace illustrative model IDs and `OWNER/REPO` before selecting those entries.

Do not store API keys, tokens, passwords, SSH keys, or privacy-consent grants in these files.
