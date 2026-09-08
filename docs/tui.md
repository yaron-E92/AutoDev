# AutoDev terminal UI

`autodev tui` is a read-mostly operator dashboard over AutoDev's existing application/state contracts. It is deliberately **not** a second queue classifier, coordinator, scheduler, privacy gate, claim implementation, or run-state machine.

The first screen combines repository identity, queue counts, the current durable run/resume boundary, branch and PR identity, local/semantic verification indicators, scheduler/backend/runtime state, notification state, privacy policy/grant counts, active running/claim count, and a navigable list of managed/open issues with dependency blockers.

When multimodal UX verification evidence exists, the run section also shows the final visual status, whether that evidence still belongs to the current semantic checkpoint, compared UX target IDs, violation/unverifiable counts, a short evidence-identity prefix, and the effective runtime/model/capability. The same fields are available in `autodev tui --once --json`. The TUI never turns those display fields into a separate authority source.

## Commands and keys

Start the interactive dashboard with:

```text
autodev tui
```

Use `Up`/`Down` (or `k`/`j`) to select an issue and `Enter` to show/hide issue detail. `r` forces a remote refresh, `o` opens the current run's PR URL, and `q` exits.

The first guarded operations are intentionally narrow:

- `m` manages the selected issue through the existing `manage_cli` contract;
- `c` reconciles the queue through `queue_workflow.reconcile_queue`;
- `n` toggles scheduler notifications through the existing scheduler notification policy store.

Every mutating action enters a confirmation state and requires an explicit `y`. Cancelling or crashing the TUI before confirmation performs no mutation. Model-work actions such as starting or resuming issue-to-PR remain on the canonical CLI in this milestone rather than making a long-running model invocation look like a UI navigation event.

For automation/tests/non-interactive terminals:

```text
autodev tui --once
autodev tui --once --json
```

`--once` uses the same structured snapshot model but does not enter raw-terminal mode. The JSON `run` object includes multimodal UX status, checkpoint-validity, evidence identity, compared target IDs, counts, and runtime/model capability metadata when present.

## Refresh and observation safety

Local state is refreshed frequently from durable AutoDev files and scheduler registration. Remote queue/GitHub inspection is bounded separately and defaults to every 30 seconds; AutoDev refuses a configured remote polling interval below 10 seconds. Manual `r` refresh is always available.

Observation does not call a model, reconcile labels, advance stages, refresh claims, or write durable run state. A remote-refresh failure leaves the last known remote queue snapshot visible and records the error instead of destroying the local dashboard.

The dashboard does not display prompts, source snippets, model transcripts, credentials, arbitrary model output, or screenshot bytes. Multimodal UX inspection exposes only AutoDev-normalized logical target IDs, bounded counts, safe runtime metadata, and hashes/identity prefixes. The optional non-success summary is read from AutoDev's existing local `non-success-report.md` artifact and truncated before display.

## Toolkit decision

Issue #206 asked for an explicit toolkit decision rather than choosing by inertia. The first implementation evaluated three approaches:

### Textual

Textual provides the richest widget/layout system and would make tables/dialogs visually polished quickly. It was not selected for the first milestone because it adds a comparatively large runtime dependency graph, changes frozen/native packaging inputs, and introduces more framework lifecycle surface than this read-mostly dashboard currently needs.

### prompt_toolkit (with or without Rich)

`prompt_toolkit` is mature, cross-platform, testable, and well suited to keyboard applications. It was the strongest third-party alternative. It was not selected initially because AutoDev currently has a dependency-light frozen runtime; adopting it solely for a small first dashboard would make packaging and supply-chain maintenance part of #206 before the product has proven it needs richer widgets. Rich composition would add a second dependency for presentation.

### Dependency-free AutoDev terminal driver — selected

The first milestone uses a small AutoDev-owned terminal driver built only on the Python standard library. Windows input uses `msvcrt`; POSIX input uses `termios`/`select`; rendering is plain text plus the minimal ANSI sequences needed for alternate-screen/cursor control. The renderer/controller is terminal-independent and is unit-tested without a real terminal.

Reasons for selecting it:

- no new runtime dependency or installer packaging work;
- works in the existing Windows and Linux frozen/native products;
- fast startup and small failure surface;
- deterministic rendering and navigation tests;
- keyboard-only by construction;
- no reliance on color, and ASCII-only content degrades cleanly in limited terminals;
- keeps the first milestone focused on authoritative state/application boundaries rather than widget-framework adoption.

This is not a permanent prohibition on Textual or prompt_toolkit. If later TUI work needs complex forms, mouse support, multi-repository grids, richer accessibility hooks, or asynchronous long-running operations, the renderer/controller/state separation intentionally leaves room to replace only the terminal adapter.

## Architecture

`automation/tui_model.py` owns immutable display models and structured observation. It consumes `repository_identity`, `queue_selection`, `queue_workflow`, `queue_presentation`, `workflow_stages`, scheduler registration/status, notification policy, privacy grant/policy APIs, and normalized multimodal UX evidence directly. It never scrapes human-formatted CLI output.

`automation/tui_terminal.py` owns presentation, keyboard navigation, confirmation state, and bounded refresh timing. Rendering is a pure function of a `TuiSnapshot` plus `ViewState`; terminal input is isolated behind `TerminalInput`.

`automation/tui_actions.py` is the narrow mutation boundary. Actions delegate to existing AutoDev service/CLI contracts rather than duplicating queue or scheduler logic. Read-only PR opening is also isolated there.

`automation/tui_cli.py` is the public `autodev tui` entrypoint and owns argument validation, snapshot bootstrap, and interactive versus `--once` mode.

Because all durable workflow state remains owned by existing AutoDev modules, a TUI exception can at worst terminate the UI process. It cannot silently advance a stage or invent a new queue/run state.
