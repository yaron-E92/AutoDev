# Outcome notifications

AutoDev can send local native notifications when issue-to-PR work reaches a material durable outcome.

Notifications are **opt-in** and are stored in user-local state per GitHub repository. Enabling notifications does not change repository configuration and does not transmit notification content to an external service.

## Configure

From a configured repository:

```text
autodev notifications enable
autodev notifications status
autodev notifications disable
```

An optional reminder cooldown can repeat an unresolved blocked/attention-required notification:

```text
autodev notifications enable --reminder-hours 24
```

Use `--json` with status when machine-readable policy and last-event diagnostics are useful.

## Manual and scheduled runs share one policy

One enabled repository policy covers:

- manual `autodev issue-to-pr ISSUE` runs;
- manual `autodev resume` runs;
- installed scheduler health/outcome events.

The older scheduler spelling remains supported for installed schedulers:

```text
autodev scheduler notifications enable
autodev scheduler notifications status
autodev scheduler notifications disable
```

Both command surfaces read and write the same existing user-local policy file:

```text
~/.autodev/schedulers/<owner>/<repo>/notifications.json
```

There is therefore no competing scheduler-only policy and no migration that copies secrets or repository data.

## Events

Manual runs notify, when enabled, on durable transitions to:

- `ready-for-review` / `PR_READY`;
- `blocked`, including `ATTENTION_REQUIRED`;
- `failed`.

Scheduler health maps its authoritative states into the same notification event contract. Existing scheduler health transitions remain observable, while `PR_READY`, attention/blocked states, and scheduler failures use the same ready/blocked/failed event categories as manual runs.

Repeated observations of the same unchanged event fingerprint are suppressed. A changed durable failure/source/PR identity is a new event and can notify again. Unresolved blocked/attention states can repeat only after the configured reminder cooldown.

## Privacy and payloads

Notification text is deterministic and bounded. Manual outcome notifications use only safe operational metadata:

- GitHub repository identity;
- issue number;
- terminal event;
- stage identifier;
- failure classification/reason code;
- PR URL when available.

They do **not** include prompts, source snippets, arbitrary model output, credentials, provider secrets, or the free-form failure reason.

Scheduler notification summaries continue to be rendered from the bounded deterministic scheduler-health snapshot.

Policy and event state are user-local. No external notification provider ships in this milestone.

## Native delivery

The native provider currently uses:

- POSIX desktops: `notify-send`, when available;
- Windows: the existing `msg.exe` path.

Delivery is best-effort. A missing desktop session, unavailable native command, nonzero notifier exit, or notifier exception is recorded as delivery diagnostics but never changes the issue-to-PR result, scheduler result, repository state, queue state, or process exit code.

The provider boundary is separate from the coordinator and event-state logic so a future Windows toast implementation or optional external provider can be added without changing authoritative run-state transitions.

## Diagnostics

`autodev notifications status --json` includes the current policy and last observed event/delivery state for manual and scheduled modes.

Manual runs also record the bounded delivery result under `notification_outcome` in the current AutoDev diagnostics file when that file is available. Delivery diagnostics never become authoritative workflow state.
