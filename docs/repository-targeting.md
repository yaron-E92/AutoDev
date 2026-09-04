# Repository targeting

AutoDev uses one GitHub repository target across queue operations, issue-to-PR workflows, privacy grants, scheduler installation/preflight, doctor/setup, and other GitHub-aware commands.

## One-shot override

Use the global `--owner` and `--repo` pair before the command when the checkout's remotes are stale, point at a fork, or otherwise do not identify the repository AutoDev should operate on:

```text
autodev --owner com-mit-group --repo ShuffleTask privacy status
autodev --owner com-mit-group --repo ShuffleTask privacy consent
autodev --owner com-mit-group --repo ShuffleTask scheduler install
autodev --owner com-mit-group --repo ShuffleTask issue-to-pr 338
```

The two options are a pair. Supplying only one is an error. They are top-level options so they do not conflict with command-specific options such as `issue-to-pr ISSUE --repo <working-directory>`.

The override is scoped to that AutoDev invocation; AutoDev restores any pre-existing `GITHUB_OWNER` / `GITHUB_REPO` environment values before returning.

## Persistent repository override

For a repository-owned persistent target, set `github_repository` in the tracked `.autodev/repo.json`:

```json
{
  "version": 1,
  "github_repository": "com-mit-group/ShuffleTask",
  "opencode": {
    "enabled": true
  }
}
```

This is especially useful after a GitHub repository transfer or rename when an existing checkout may still contain an old remote URL. Dedicated scheduler workers receive the same committed repository configuration, so source and worker resolve the same privacy-grant identity.

## Precedence

Repository targeting resolves in this order:

1. global `--owner OWNER --repo REPO` for the current invocation;
2. `GITHUB_OWNER` + `GITHUB_REPO` environment overrides;
3. `.autodev/repo.json` `github_repository`;
4. the supported GitHub remote / command-specific fallback used by the caller.

Existing command-specific `--github-repo OWNER/REPO` options remain supported. When a command explicitly passes that value to the shared resolver it is authoritative for that command; the global pair is intended to provide the same repository context across the entire AutoDev invocation.

## Privacy grants and transfers

Persistent privacy consent is repository-scoped. Privacy grant creation, inspection, revocation, runtime authorization, and scheduler headless preflight now use the same canonical repository resolver instead of independently deriving identity from the raw local `origin`.

AutoDev does not silently migrate grants between unrelated repository identities. After intentionally changing the selected repository target, create consent for that selected identity if no active grant exists for it.
