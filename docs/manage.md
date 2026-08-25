# Managing issues with AutoDev

`autodev manage` is the explicit operator-facing way to opt GitHub issues into AutoDev's management scope.

## Managed is authorization, not readiness

The `autodev:managed` label means that the repository operator permits AutoDev to consider an issue for autonomous work. It is deliberately different from `autodev:ready`.

AutoDev derives readiness separately from queue state, blockers, attention state, active ownership, and repository policy. Running `autodev manage` therefore does **not**:

- add `autodev:ready`;
- change `blocks` / `blocked by` relationships;
- claim the issue;
- start an issue-to-PR run; or
- invoke a model provider.

After issues are managed, `autodev queue reconcile` remains the operation that derives queue labels from authoritative state.

## Manage one issue

From a configured target repository:

```text
autodev manage 123
```

The CLI also accepts the `#123` form. Quote it in shells where `#` begins a comment:

```text
autodev manage '#123'
```

The issue must be open. If it already has `autodev:managed`, the command succeeds without changing it. Existing labels are preserved.

Before a mutating manage operation, AutoDev uses the same canonical queue-label bootstrap as `autodev repo ensure-labels`, so a missing `autodev:managed` label definition is created consistently with the rest of the queue implementation.

## Manage every open issue

```text
autodev manage --all
```

This adds `autodev:managed` to every currently open GitHub issue. Pull requests are excluded, existing labels are preserved, and already-managed issues are left unchanged.

Human-readable output reports separate counts for newly managed and already managed issues. For automation:

```text
autodev manage --all --json
```

## List the managed set

```text
autodev manage --list
```

`--list` shows only currently open issues carrying `autodev:managed`. It is intentionally read-only: it does not bootstrap labels, reconcile queue state, or mutate GitHub.

Machine-readable output is available with:

```text
autodev manage --list --json
```

The managed set is the authorization boundary from which queue and scheduler policy may later select eligible work; it is not itself a runnable queue.

## Repository selection

By default AutoDev resolves the GitHub repository from the current target repository. The usual explicit location options are available:

```text
autodev manage 123 --repo ../my-project
autodev manage --list --github-repo owner/repository
```

Repository-resolution or GitHub-authentication failures are reported as command errors rather than silently operating on another repository.
