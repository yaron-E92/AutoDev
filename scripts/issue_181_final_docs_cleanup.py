from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def write(path: str, text: str) -> None:
    target = ROOT / path
    target.write_text(text.rstrip() + "\n", encoding="utf-8")


def delete(path: str) -> None:
    target = ROOT / path
    if not target.is_file():
        raise SystemExit(f"missing obsolete file: {path}")
    target.unlink()


def rewrite_readme() -> None:
    write(
        "README.md",
        r'''# AutoDev

AutoDev is a resumable issue-to-PR automation system. Python owns workflow state and deterministic transitions; model runtimes are isolated behind role boundaries. The supported user-facing entrypoint is the `autodev` CLI.

## Install

From an AutoDev checkout:

```text
python -m automation.autodev_cli install --user
```

Add the launcher directory to `PATH`, or let AutoDev add its bounded user-profile block:

```text
python -m automation.autodev_cli install --user --add-to-path
```

Then configure a target repository:

```text
autodev repo install
```

OpenCode assets are installed by default. Use `autodev repo install --no-opencode` for repositories that deliberately do not use OpenCode.

See `docs/installation.md` for the ownership and uninstall contract.

## Run an issue

```text
autodev coordinate --arguments 123
```

Resume durable state after an interruption:

```text
autodev resume
```

The coordinator owns Reader, Synthesizer, Planner, Implementer, Fixer, Verifier, deterministic verification, optional Windows verification, semantic repair, PR creation, CI observation, and durable terminal state. AutoDev does not merge its pull request automatically.

With OpenCode installed, the equivalent frontend commands are:

```text
/autodev-issue-to-pr 123
/autodev-resume
```

OpenCode role/model mappings come from `opencode.json` / `opencode.jsonc`. AutoDev does not maintain a second OpenCode model-routing configuration.

## Autonomous queue and scheduler

Queue operations are first-class CLI commands:

```text
autodev queue status
autodev queue next
autodev queue reconcile
```

Scheduler installation is an explicit per-repository opt-in:

```text
autodev scheduler install
autodev scheduler status
autodev scheduler health
autodev scheduler run-once
autodev scheduler uninstall
```

Roadmap priority, distributed claims, resumable runs, privacy readiness, and terminal attention states remain deterministic scheduler inputs. See `docs/queue.md` and `docs/scheduler.md`.

## Privacy

AutoDev defaults to strict handling of repository content. Provider/runtime authorization is checked before model work, and persistent grants are explicit, scoped, revocable, and user-local.

```text
autodev privacy ...
```

See `docs/privacy.md` for policy, consent, grants, and enforcement details.

## Verification

The normal workflow can include:

```text
implementation
  -> local deterministic verification
  -> required platform verification, when configured
  -> semantic verifier
  -> targeted repair when needed
  -> PR / CI
```

Semantic verifier output is strict JSON. Current semantic prompt templates are `promptTemplates/semantic-verifier.md` and `promptTemplates/semantic-repair.md`. See `docs/semantic-verification.md` and `docs/windows-verification.md`.

## Architecture

Production Python is split into responsibility-oriented modules under `automation/` and `area_reader/`. Permanent architecture tests reject giant modules, local import cycles, retired module paths, stale maintained-doc references, issue-migration scaffolding, and chunk artifacts.

See `docs/python-architecture.md`.

## Development

Run the maintained Python checks with:

```text
python -m compileall -q automation area_reader tests
python -m unittest discover -s tests -v
```

Repository CI additionally checks Linux and Windows Python matrices, canonical CLI smoke tests, workflow references, shell syntax, release reproducibility, version intent, repository hygiene, and exact-source Windows verification.

Contribution guidance lives in `CONTRIBUTING.md`.
''',
    )


def rewrite_contributing() -> None:
    write(
        "CONTRIBUTING.md",
        r'''# Contributing to AutoDev

AutoDev's supported product surface is the `autodev` CLI plus the maintained OpenCode, scheduler, Windows-verification, CI, and release integrations. Do not reintroduce source-checkout-only launchers or compatibility implementations after they have been retired.

## Development checks

Before opening or updating a PR, run:

```text
python -m compileall -q automation area_reader tests
python -m unittest discover -s tests -v
```

Also run the relevant platform or workflow checks for files you touched. CI covers the supported Linux/Windows Python matrix, canonical CLI smoke tests, PowerShell/Bash syntax, workflow lint and immutable external action refs, release reproducibility, version intent, repository hygiene, and exact-source Windows verification.

## Architecture rules

`tests/test_python_architecture.py` is a permanent guardrail. In production Python under `automation/` and `area_reader/`:

- keep responsibility modules below the configured giant-module threshold;
- keep the top-level local import graph acyclic;
- depend on owning responsibility modules rather than aggregate compatibility facades;
- do not restore paths explicitly listed as removed;
- do not add temporary issue-migration workflows/scripts to the finished tree;
- do not commit `*.chunk*.txt` artifacts.

When a migration leaves an old path unused, delete it instead of keeping an indefinite shim unless a current supported entrypoint demonstrably requires it.

## Workflow behavior

Python owns deterministic sequencing, durable state, resume decisions, verification boundaries, repair budgets, PR/CI progression, and terminal outcomes. Model runtimes should receive bounded role inputs and must not become owners of workflow transitions.

The canonical command for a normal issue run is:

```text
autodev coordinate --arguments 123
```

Resume with:

```text
autodev resume
```

OpenCode is an optional frontend over the same workflow. Its model mapping remains in `opencode.json` / `opencode.jsonc`.

## Prompt templates

Maintained workflow templates live in `promptTemplates/`. Template placeholders use the canonical delimiter:

```text
{~{IssueText}~}
{~{Plan}~}
```

Do not restore the retired `{{Name}}` compatibility syntax. Current semantic templates are:

```text
promptTemplates/semantic-verifier.md
promptTemplates/semantic-repair.md
```

The durable repair artifact `.autodev-run/current/verification-repair.md` is run state, not a prompt-template file.

## Tests

Delete tests that only exercise deleted compatibility behavior. Preserve or retarget tests that protect a still-supported contract. A cleanup PR should prove both that the obsolete path is gone and that current behavior remains covered.

Prefer focused tests for the owning module plus the full unit suite before finalizing broad refactors.

## Documentation

Document only current supported entrypoints. Git history is the archive for retired commands and architecture. If code is deleted, remove instructions that tell users to invoke it.

## Version intent

PRs must state release intent using the repository's version-intent convention. Behavior-preserving cleanup such as dead-code removal uses:

```text
+semver: none
```

Use a release-advancing intent only when the change actually warrants one.
''',
    )


def rewrite_model_roles() -> None:
    write(
        "docs/model-roles.md",
        r'''# Model roles

AutoDev separates deterministic workflow ownership from model execution. The model-backed roles are:

```text
reader
synthesizer
planner
implementer
fixer
verifier
```

The coordinator is not a free-form model planner; Python selects the next deterministic workflow transition.

## OpenCode model routing

For OpenCode runs, OpenCode configuration is authoritative. Configure role models through `opencode.json` or `opencode.jsonc`, for example:

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

Roles may be mapped independently. Unspecified roles use normal OpenCode inheritance. AutoDev does not duplicate this mapping in `.autodev` and does not support ad-hoc per-run model override flags that would create a competing routing layer.

`automation.opencode_adapter_models` resolves and validates the effective OpenCode mapping without exposing secrets.

## Prompt policy

AutoDev applies a role-specific prompt policy derived from Ponytail principles while preserving explicit issue requirements and output contracts. Current default modes are:

```text
reader       off
synthesizer  lite
planner      lite
implementer  full
fixer        full
verifier     review
```

The policy is an AutoDev-native adaptation: reader minimization is disabled, verifier policy is review-only, and safety/data-integrity requirements always override minimization.

A provider-profile JSON may still carry the current `prompt_policy` and Headroom metadata used when AutoDev prepares role context. It does not replace OpenCode's role/model mapping.

## Headroom

Headroom is optional. AutoDev's context-optimization layer can use Headroom settings to describe/compress eligible evidence while preserving issue requirements, role policy, patch markers, and verifier output contracts. Direct OpenCode transport remains owned by OpenCode.

See `docs/headroom.md` for the current configuration and diagnostic model.

## Privacy

Runtime authorization occurs before model work. AutoDev records safe route/policy metadata but does not persist prompt content or credential values in privacy audit records. Persistent consent grants are explicit and scoped.

See `docs/privacy.md`.

## Role boundaries

Each role has a bounded preparation/acceptance contract under `.autodev-run/current`. The runtime must produce an accepted durable artifact before Python advances. A zero process exit without a valid accepted artifact is not success.

See `docs/opencode.md`, `docs/role-runtimes.md`, and `docs/python-architecture.md`.
''',
    )


def rewrite_semantic_verification() -> None:
    write(
        "docs/semantic-verification.md",
        r'''# Semantic verification gate

AutoDev runs semantic verification as a deterministic workflow stage after required deterministic/platform verification and before final PR/CI progression.

## Order

```text
implementation
  -> local deterministic verification
  -> required Windows/platform verification, when configured
  -> semantic verifier
       -> pass: continue
       -> blocked: stop with durable evidence
       -> repair: targeted fixer boundary
            -> deterministic verification again
            -> semantic verification again
  -> PR / CI
```

Any repair changes source identity and therefore requires the relevant verification boundaries again.

## Verifier contract

The verifier receives bounded issue-relevant evidence and writes strict JSON. Allowed verdicts are:

```text
pass
repair
blocked
```

Requirement statuses are `met`, `missing`, or `uncertain`. Finding severities are `blocking` or `warning`.

A `pass` result is rejected when a requirement is not `met` or a blocking finding exists. A `repair` result requires a targeted non-empty repair brief. Malformed output never defaults to pass.

The canonical verifier template is:

```text
promptTemplates/semantic-verifier.md
```

The canonical repair template is:

```text
promptTemplates/semantic-repair.md
```

Template placeholders use `{~{Name}~}` syntax.

## Durable artifacts

Semantic state is stored beneath `.autodev-run/current`. Important artifacts include the verifier input/result, deterministic evidence, repair budget state, and when repair is required:

```text
.autodev-run/current/verification-repair.md
```

That file is a generated run artifact for the Fixer. It is not a prompt template.

Repair-budget ownership lives in the `repair_budget_*` modules, so resume preserves the effective budget and final diagnosis rather than resetting a blocked run accidentally.

## Resume

Interrupted semantic work resumes through the normal durable coordinator:

```text
autodev resume
```

The coordinator validates source/artifact identity before selecting the next verifier, fixer, platform-verification, or PR/CI boundary.

## Privacy

The Verifier is a normal AutoDev role for privacy purposes. Its route must be authorized before model execution. Current role/runtime privacy policy and grants apply exactly as they do to Reader, Planner, Implementer, and Fixer.

See `docs/privacy.md`, `docs/semantic-repair-budgets.md`, and `docs/windows-verification.md`.
''',
    )


def rewrite_python_coordinator() -> None:
    write(
        "docs/opencode-python-coordinator.md",
        r'''# Deterministic OpenCode coordinator

AutoDev's OpenCode frontend uses the same Python-owned workflow as the first-class `autodev` CLI. OpenCode runs isolated role agents; Python owns stage sequencing, durable state, verification, repair counters, resume selection, PR/CI progression, and terminal outcomes.

## Install

From the target repository:

```text
autodev repo install
```

This installs the maintained `.opencode/commands/` and `.opencode/agents/` assets. Model routing remains in `opencode.json` / `opencode.jsonc`.

## Run

Inside OpenCode:

```text
/autodev-issue-to-pr 123
```

Resume with:

```text
/autodev-resume
```

The installed commands invoke the first-class AutoDev launcher rather than a repository-local Python bridge.

## Role execution

For model-backed work Python selects the role, prepares its bounded durable input, launches the configured runtime/agent, validates the output contract, records accepted-artifact identity, and only then advances.

A successful child-process exit without a valid accepted artifact fails closed. Protocol correction is bounded and remains part of the deterministic coordinator contract.

Standalone `/autodev-read`, `/autodev-plan`, `/autodev-implement`, `/autodev-fix`, and `/autodev-verify` commands remain available for intentional role-level debugging/intervention.

AutoDev never merges the resulting pull request automatically.
''',
    )


def trim_other_docs() -> None:
    path = ROOT / "docs/windows-verification.md"
    text = path.read_text(encoding="utf-8")
    old = '''Use `python3` where appropriate. The older command:\n\n```text\npython -m automation.opencode_adapter install\n```\n\nis deprecated and remains only as a compatibility shim that delegates to the canonical installer.\n\n'''
    text = text.replace(old, "Use `python3` where appropriate.\n\n")
    path.write_text(text, encoding="utf-8")

    path = ROOT / "docs/opencode.md"
    text = path.read_text(encoding="utf-8")
    start = text.find("### OpenCode mapping vs headless AutoDev provider profiles\n")
    end = text.find("## Ponytail and Headroom\n", start)
    if start >= 0 and end >= 0:
        text = text[:start] + text[end:]
    start = text.find("## Existing workflows remain independent\n")
    if start >= 0:
        text = text[:start].rstrip() + "\n"
    path.write_text(text, encoding="utf-8")


def retarget_tests_and_guards() -> None:
    path = ROOT / "tests/test_headroom.py"
    text = path.read_text(encoding="utf-8")
    text = text.replace('TEMPLATES / "verifier.md"', 'TEMPLATES / "semantic-verifier.md"')
    path.write_text(text, encoding="utf-8")

    path = ROOT / "tests/test_python_architecture.py"
    text = path.read_text(encoding="utf-8")
    marker = '    "automation/prompt_runner.py",\n'
    if marker in text and '    "automation/model_roles.py",\n' not in text:
        text = text.replace(marker, marker + '    "automation/model_roles.py",\n')
    path.write_text(text, encoding="utf-8")


def main() -> None:
    delete("promptTemplates/verifier.md")
    delete("promptTemplates/verification-repair.md")
    rewrite_readme()
    rewrite_contributing()
    rewrite_model_roles()
    rewrite_semantic_verification()
    rewrite_python_coordinator()
    trim_other_docs()
    retarget_tests_and_guards()


if __name__ == "__main__":
    main()
