from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def write(path: str, text: str) -> None:
    (ROOT / path).write_text(text.rstrip() + "\n", encoding="utf-8")


def migrate_verification_profile_template() -> None:
    path = ROOT / "automation" / "workflow_prompts.py"
    text = path.read_text(encoding="utf-8")
    replacements = {
        'template.replace("{{ProfilesCsv}}", profiles_csv)': 'template.replace("{~{ProfilesCsv}~}", profiles_csv)',
        '.replace("{{AutomationRoot}}", str(autodev_root))': '.replace("{~{AutomationRoot}~}", str(autodev_root))',
        '.replace("{{CodexToolsDir}}", codex_tools)': '.replace("{~{CodexToolsDir}~}", codex_tools)',
    }
    for old, new in replacements.items():
        if old not in text:
            raise SystemExit(f"missing verification-profile renderer marker: {old}")
        text = text.replace(old, new, 1)
    path.write_text(text, encoding="utf-8")

    profile_path = ROOT / "codex-profiles.json"
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    template = str(profile.get("verifyCommandTemplate", ""))
    template = template.replace("{{CodexToolsDir}}", "{~{CodexToolsDir}~}")
    template = template.replace("{{ProfilesCsv}}", "{~{ProfilesCsv}~}")
    template = template.replace("{{AutomationRoot}}", "{~{AutomationRoot}~}")
    profile["verifyCommandTemplate"] = template
    profile_path.write_text(json.dumps(profile, indent=2) + "\n", encoding="utf-8")


def rewrite_headroom() -> None:
    write(
        "docs/headroom.md",
        '''# Optional Headroom compression

Headroom is an optional context-compression layer. It is not required for AutoDev and it does not own workflow sequencing, model routing, privacy policy, or durable state.

## Safety model

AutoDev compresses only prompt sections with known boundaries. Requirements, role/safety instructions, output contracts, and other protected control text stay intact. If a prompt shape is unknown, AutoDev leaves it uncompressed instead of guessing.

For supported prompt shapes AutoDev:

1. identifies compressible evidence ranges;
2. sends only those ranges to the configured local Headroom `/v1/compress` endpoint;
3. reassembles the prompt with protected text unchanged;
4. records safe compression telemetry without prompt contents or credentials.

The canonical semantic verifier uses `promptTemplates/semantic-verifier.md`; its synthesized handoff, plan, changed files, diff, deterministic evidence, cross-file evidence, and uncertainty notes are independently bounded evidence sections, while the issue, acceptance criteria, and JSON output contract remain protected.

## Configuration

A provider-profile JSON may contain a top-level `headroom` section:

```json
{
  "headroom": {
    "enabled": true,
    "proxy_url": "http://127.0.0.1:8787/v1",
    "mode": "lossless",
    "output_shaping": false,
    "fail_open": true,
    "roles": {
      "reader": { "enabled": true },
      "planner": { "enabled": true },
      "implementer": { "enabled": true },
      "fixer": { "enabled": true },
      "verifier": { "enabled": false }
    }
  }
}
```

`mode` must remain `lossless` and output shaping must remain disabled. Verifier compression defaults to disabled unless explicitly enabled for that role.

Provider-profile Headroom settings affect prompt preparation/compression metadata. OpenCode model routing remains owned by effective `opencode.json` / `opencode.jsonc` configuration.

## Running with OpenCode

Start Headroom separately when you intentionally use it, then launch OpenCode through the wrapper:

```text
headroom wrap opencode
```

Run AutoDev normally inside OpenCode:

```text
/autodev-issue-to-pr 123
```

Or use the first-class CLI outside the OpenCode command frontend:

```text
autodev coordinate --arguments 123
```

Headroom must never become a requirement for ordinary AutoDev execution.

## Failure behavior

Compression is fail-open only when configured that way: a compression-only failure may fall back to the original prompt. Provider/authentication/rate-limit/model failures remain provider failures and are not reclassified as compression failures or silently rerouted to another model.

## Telemetry

AutoDev stores only safe compression metadata such as status, mode, section count, timing, hashes, and provider-reported compression metrics. Prompt contents and credential values are not written to telemetry.
''',
    )


def rewrite_opencode_resume() -> None:
    write(
        "docs/opencode-resume.md",
        '''# OpenCode status and resume

OpenCode chat history is disposable. AutoDev persists workflow progress beneath:

```text
.autodev-run/current/
```

`run-manifest.json` is the durable checkpoint/invalidation record. `state.json` and the bounded role/verification artifacts provide execution evidence for the shared Python workflow.

## Status

Inside OpenCode:

```text
/autodev-status
```

Outside the OpenCode command frontend, use the first-class CLI:

```text
autodev status
```

Status is read-only. It reports the current issue/run identity, completed and next boundaries, failure information, resume safety, source/PR/CI identity, repair counters, and safe runtime/model metadata.

## Resume

Inside OpenCode:

```text
/autodev-resume
```

Or:

```text
autodev resume
```

Resume validates durable artifact hashes, repository/base/branch identity, source identity, shipped PR/CI proof when present, and execution fingerprints before selecting another role or deterministic stage. It never reconstructs progress from chat history.

Completed reader, synthesizer, planner, implementer, verification, and PR/CI boundaries are not rerun merely because OpenCode restarted.

## Role/runtime changes

OpenCode role/model selection remains owned by `opencode.json` / `opencode.jsonc`. Runtime selection follows the normal AutoDev runtime configuration. Changing an execution-affecting model/runtime for already-completed work requires explicit invalidation before resume; AutoDev does not layer new role output over stale accepted artifacts.

Use the status command to inspect the effective next boundary before resuming.

## Repair counters

Local, semantic, Windows/platform, and CI repair state is durable. An interrupted repair resumes at the matching fixer/verification boundary with the persisted attempt count rather than resetting the budget.

## Missing manifest

AutoDev does not infer trustworthy history for an old/incomplete `.autodev-run/current` directory that lacks the required manifest. Status/resume fail clearly instead of guessing which work already happened.
''',
    )


def rewrite_runtime_hardening() -> None:
    write(
        "docs/opencode-runtime-hardening.md",
        '''# OpenCode runtime hardening

OpenCode is AutoDev's default role runtime. Python owns deterministic workflow state; OpenCode owns model execution for installed `autodev-*` agents.

## User-owned OpenCode configuration

Root `opencode.json` and `opencode.jsonc` are supported user-owned runtime/model configuration. During OpenCode execution those exact root files are excluded from product-source drift checks because their effective model identity is captured separately in role fingerprints.

This exclusion is narrow. Similar files elsewhere in the repository remain normal source changes.

## First-class launcher

Installed OpenCode commands invoke the first-class `autodev` launcher. Do not reintroduce repository-local Python bridge launchers, bridge configuration files, interpreter probing, alternate bridge copies, or shell wrappers around the canonical command.

## Durable role acceptance

A child process exiting successfully is not workflow proof. Python validates the role's acceptance record and, for file-backed outputs, the accepted artifact hash before dependent work can advance.

Role diagnostics are bounded to safe runtime/model identity and artifact state. They do not dump prompts, hidden reasoning, credentials, or unbounded transcripts.

## Headroom diagnostics

Headroom remains optional. When expected, AutoDev may report bounded health/routing diagnostics, but a Headroom problem is not permission to change provider/model routing and is not treated as a code-repairable repository defect.

## Terminal failure preservation

Runtime failures retain their originating stage, classification, reason, and bounded fingerprint. Later success clears stale transient failure context so an unrelated failure cannot inherit old diagnostics.

## Resume authority

`/autodev-resume` and `autodev resume` delegate continuation decisions to the Python resume engine. Returned next role/stage and repair counters are authoritative. AutoDev fails closed if it cannot derive an authoritative next boundary from durable state.

```text
Python owns workflow state and boundaries.
OpenCode owns model-heavy role execution.
No chat process may invent durable progress.
```
''',
    )


def rewrite_role_runtimes() -> None:
    write(
        "docs/role-runtimes.md",
        '''# AutoDev role runtimes

AutoDev's Python coordinator owns ordering, durable state, verification, repair budgets, shipment, and resume. Model-heavy work executes through a role-runtime boundary.

```text
Python coordinator/state machine
        -> role runtime
        -> role/model execution
```

The runtime cannot decide which workflow stage runs next and cannot make artifact acceptance authoritative.

## Default runtime

`opencode` is the default runtime. It launches the installed AutoDev role agents and reads their effective model mapping from `opencode.json` / `opencode.jsonc`.

## Selecting a runtime

Precedence is:

1. explicit `--runtime`;
2. `AUTODEV_ROLE_RUNTIME`;
3. repository `.autodev/config.json` `role_runtime`;
4. user AutoDev configuration `role_runtime`;
5. `opencode`.

Example:

```text
autodev coordinate --arguments 123 --runtime opencode
```

An unknown explicitly selected runtime fails before model work. AutoDev does not silently fall back to another runtime.

## Runtime identity and resume

The selected runtime contributes to safe execution fingerprints. Changing the runtime for already-completed role work follows the same invalidation rules as changing other execution-affecting role configuration. A rejected switch does not overwrite the accepted manifest identity.

## Runtime contract

A runtime supplies safe execution identity plus a bounded invocation result. Python remains responsible for preparation, output validation, protocol correction, repair budgets, source identity, commits, PR/CI, and durable failure diagnostics.

Adding another production runtime requires an explicit registry/installation/configuration contract. Naming an unregistered runtime does not enable it.
''',
    )


def rewrite_semantic_budgets() -> None:
    write(
        "docs/semantic-repair-budgets.md",
        '''# Semantic repair budgets

Semantic repair is bounded. A verifier may return `repair`, but AutoDev only performs the configured number of automatic semantic repairs before stopping for human action.

## Fixed policy

`fixed` is the default:

```text
SEMANTIC_REPAIR_BUDGET_POLICY=fixed
MAX_SEMANTIC_REPAIR_ATTEMPTS=2
```

When the budget is exhausted, AutoDev preserves the final repair brief, requirement/finding evidence, verified source identity, verification-result path, and stable failure fingerprint. The terminal classification remains `repair-budget-exhausted` rather than discarding the underlying semantic diagnosis.

## Adaptive policy

Adaptive budgeting is opt-in. It derives a bounded effective limit from verified issue-scoped changed lines and configured minimum/base/maximum values. Generated/build/cache paths and binary changes are excluded; tests and documentation are weighted below source code.

The computed inputs, formula version, caps, and effective limit are persisted. Resume reuses the persisted budget instead of silently recomputing it from a changed environment.

## Raising an exhausted budget

An operator may explicitly increase a blocked run's limit and resume:

```text
MAX_SEMANTIC_REPAIR_ATTEMPTS=4 autodev resume
```

Existing attempts remain counted. Lower values never shrink a persisted budget. For adaptive policy, increasing the configured adaptive cap may raise a cap-limited run.

## Durable evidence

Canonical semantic output remains:

```text
.autodev-run/current/verification-result.json
```

When repair is required AutoDev prepares:

```text
.autodev-run/current/verification-repair.md
```

That is a generated run artifact, not a prompt-template source file.

## Safety

Repair-budget changes do not bypass deterministic/platform verification, reset counters, approve verifier failures, or create unlimited retry loops. A repair modifies source identity and therefore re-enters the required verification boundaries before semantic review continues.
''',
    )


def rewrite_opencode() -> None:
    write(
        "docs/opencode.md",
        '''# OpenCode integration

OpenCode is AutoDev's default model-role runtime and an optional command frontend. Python remains the owner of deterministic workflow sequencing, durable state, verification, repair budgets, PR/CI progression, and resume.

## Install into a target repository

```text
autodev repo install
```

This installs the maintained `.opencode/commands/` and `.opencode/agents/` assets. Root `opencode.json` / `opencode.jsonc` remain user-owned and are the authority for OpenCode model mapping.

## Run an issue

Inside OpenCode:

```text
/autodev-issue-to-pr 123
```

Equivalent first-class CLI:

```text
autodev coordinate --arguments 123
```

Resume/status:

```text
/autodev-status
/autodev-resume
```

or:

```text
autodev status
autodev resume
```

AutoDev never merges the resulting PR automatically.

## Role model mapping

Configure models through normal OpenCode configuration:

```json
{
  "agent": {
    "autodev-reader": { "model": "provider/reader-model" },
    "autodev-planner": { "model": "provider/planner-model" },
    "autodev-implementer": { "model": "provider/implementer-model" },
    "autodev-verifier": { "model": "provider/verifier-model" }
  }
}
```

Roles may be mapped independently. Unmapped roles inherit normal OpenCode configuration. AutoDev rejects unsupported ad-hoc per-run model overrides instead of creating a competing model-routing layer.

Use `/autodev-models` when installed to inspect the effective safe role/model mapping.

## Role boundaries

Python prepares bounded role input, launches the selected runtime/agent, validates the role output/acceptance record, and only then advances. A zero exit code without a valid accepted artifact is not success.

Standalone role commands remain available for deliberate intervention/debugging:

```text
/autodev-read 123
/autodev-plan 123
/autodev-implement 123
/autodev-fix 123
/autodev-verify 123
```

## Privacy

Provider/runtime authorization happens before model work. API keys remain in the normal provider/OpenCode/user environment; do not place credentials in AutoDev command/agent files or repository documentation.

Persistent privacy grants are explicit, scoped, revocable, and user-local. See `docs/privacy.md`.

## Prompt policy and Headroom

AutoDev applies its role-specific prompt policy while preparing role context. Headroom is optional and may compress only known evidence ranges. Neither mechanism changes OpenCode's model mapping.

See `docs/model-roles.md` and `docs/headroom.md`.

## Resume and failure handling

Durable state lives under `.autodev-run/current`. Restarting OpenCode does not require replaying completed work; `/autodev-resume` delegates to Python's checkpoint/resume engine and fails closed on source/artifact/fingerprint drift.

See `docs/opencode-resume.md` and `docs/opencode-runtime-hardening.md`.
''',
    )


def clean_contributing_negative_literal() -> None:
    path = ROOT / "CONTRIBUTING.md"
    text = path.read_text(encoding="utf-8")
    text = text.replace(
        "Do not restore the retired `{{Name}}` compatibility syntax. Current semantic templates are:\n",
        "Use the canonical placeholder syntax consistently. Current semantic templates are:\n",
    )
    path.write_text(text, encoding="utf-8")


def main() -> None:
    migrate_verification_profile_template()
    rewrite_headroom()
    rewrite_opencode_resume()
    rewrite_runtime_hardening()
    rewrite_role_runtimes()
    rewrite_semantic_budgets()
    rewrite_opencode()
    clean_contributing_negative_literal()


if __name__ == "__main__":
    main()
