# Windows helper scripts

These scripts are implementation helpers for canonical AutoDev Windows verification. They are not alternative issue-to-PR entrypoints.

- `windows-verification-worker.ps1` executes the exact-source Windows verification request used by GitHub Actions.
- `configure-nuget-source.ps1` configures an optional authenticated package source for repository-specific Windows verification setup.

Normal AutoDev operation starts through the installed `autodev` CLI.
