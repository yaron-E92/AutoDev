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

## Local privacy and tool configuration

Repository-local machine/user configuration may safely remain Git-excluded when it is not intended for the PR. For example, a project can place local privacy/provider/tooling paths in `.git/info/exclude`:

```text
.autodev/
.serena/
opencode.jsonc
```

If those files are **untracked and Git-excluded**, changing them does not alter `VerifiedSourceIdentity`, does not appear in `VerifiedChanges`, and cannot enter an AutoDev GitHub-API commit.

This is useful for local privacy attestations, provider settings, editor/agent memories, and other machine-specific state. It is not a secrets-management mechanism by itself; sensitive values should still use the appropriate secret store/environment mechanism.

If a file is tracked, Git considers it repository content even if an ignore rule later matches it. AutoDev therefore continues to verify and ship changes to that tracked file.

## API shipment safety

The GitHub API commit path consumes the same canonical workspace scope used by verification. AutoDev also rejects a caller-supplied non-deletion path that falls outside that scope, so an ignored untracked file cannot be smuggled into a shipment by bypassing normal change discovery.

Deleted paths that were present in the verified baseline remain eligible so repair cycles after an earlier API-created parent commit can delete files that AutoDev previously shipped.

## Non-Git fixtures

Some tests and helper fixtures intentionally operate on plain directories. When a directory is not a Git worktree, AutoDev retains the previous filesystem-walk fallback and its existing operational exclusions such as `.autodev-run/`, build outputs, virtual environments, and `memory.md`.

The fallback is for non-Git compatibility only. Production Git repositories use Git's own tracked/nonignored-untracked view.
