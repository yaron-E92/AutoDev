# TUI guarded operations

The TUI intentionally begins as a supervision surface. It exposes only a narrow set of mutations whose authoritative implementation already exists elsewhere in AutoDev.

| Key | Operation | Authoritative contract | Confirmation |
| --- | --- | --- | --- |
| `m` | Manage selected issue | `manage_cli.run_cli` / canonical queue labels | required |
| `c` | Reconcile queue | `queue_workflow.reconcile_queue` | required |
| `n` | Toggle scheduler notifications | scheduler notification policy storage | required |
| `o` | Open current PR URL | local durable `PrUrl` + OS browser | not mutating |

The TUI does not directly edit `.autodev` policy, durable run state, issue labels, scheduler registration, claims, or privacy grants. Guarded actions delegate to the same code paths used outside the TUI.

Long-running model operations (`issue-to-pr`, `resume`, repair/fix work) remain on the canonical CLI in the first TUI milestone. This keeps the UI process model-free during ordinary observation and avoids turning a synchronous model workflow into an apparently innocuous navigation keystroke. A later phase may add these operations through an explicit job/controller abstraction with the same consent, checkpoint, and failure semantics as the CLI.
