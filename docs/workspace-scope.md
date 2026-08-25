# Workspace identity and shipment scope

AutoDev uses one canonical file universe for deterministic source identity, source-drift checks, change lists, resume prerequisites, and GitHub API shipment.

## Git repositories

For a real Git worktree, **Git is authoritative**. AutoDev enumerates the workspace with the equivalent of:

```bash
git ls-files --cached --others --exclude-standard -z
```

That means:

- tracked files are included, even if a later ignore rule matches their path;
- non-ignored untracked files are included so newly created source/tests can be verified and shipped;
- untracked files ignored by `.gitignore`, `.git/info/exclude`, or the configured global excludes file are outside AutoDev's source/shipment scope;
- deleted tracked paths remain part of Git's cached path universe, allowing snapshot comparison to record deletions;
- NUL-delimited path records preserve spaces, newlines, and non-ASCII path names without line parsing.

AutoDev does not independently parse Git ignore files. This avoids divergent interpretations between verification and Git.

## Repository-owned AutoDev policy versus local state

The `.autodev/` directory is the target repository's AutoDev policy/configuration boundary. Files such as `.autodev/repo.json`, `.autodev/queue.json`, `.autodev/roadmap.yaml`, and `.autodev/privacy.json` are repository-owned configuration and should normally be committed when the repository depends on them. Scheduled workers in particular must see the same committed repository policy as the interactive checkout.

Do **not** treat the whole `.autodev/` directory as machine-local scratch configuration.

Machine/user-specific state belongs outside the repository-owned policy boundary. Examples include:

- persistent privacy consent grants in AutoDev's user-local state;
- provider credentials in the provider/OpenCode/user environment or appropriate secret store;
- scheduler registrations and worker identity in user-local AutoDev state;
- optional user-owned OpenCode configuration such as an untracked `opencode.jsonc`, when the repository deliberately keeps it local;
- editor/agent caches or memories that the project does not intend to ship.

Those machine-local files may safely remain Git-excluded when they are not intended for the PR. For example, a project can place an untracked local `opencode.jsonc` or tool-specific cache path in `.git/info/exclude`.

If such a file is **untracked and Git-excluded**, changing it does not alter `VerifiedSourceIdentity`, does not appear in `VerifiedChanges`, and cannot enter an AutoDev GitHub-API commit. This is not a secrets-management mechanism by itself; sensitive values should still use the appropriate secret store/environment mechanism.

If a file is tracked, Git considers it repository content even if an ignore rule later matches it. AutoDev therefore continues to verify and ship changes to that tracked file.

## Run state

`.autodev-run/` is durable execution state, not repository policy. It contains checkpoints and bounded run evidence used for safe resume. It should not be folded into committed `.autodev/` configuration or deliberately shipped as source changes.

## API shipment safety

The GitHub API commit path consumes the same canonical workspace scope used by verification. AutoDev also rejects a caller-supplied non-deletion path that falls outside that scope, so an ignored untracked file cannot be smuggled into a shipment by bypassing normal change discovery.

Deleted paths that were present in the verified baseline remain eligible so repair cycles after an earlier API-created parent commit can delete files that AutoDev previously shipped.

## Non-Git fixtures

Some tests and helper fixtures intentionally operate on plain directories. When a directory is not a Git worktree, AutoDev retains the filesystem-walk fallback and its operational exclusions such as `.autodev-run/`, build outputs, virtual environments, and `memory.md`.

The fallback is for non-Git compatibility only. Production Git repositories use Git's own tracked/nonignored-untracked view.
