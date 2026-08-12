# Deterministic OpenCode Python coordinator

The canonical OpenCode issue-to-PR flow uses Python for workflow sequencing. OpenCode remains the model frontend for the isolated Reader, Synthesizer, Planner, Implementer, Fixer, and Verifier agents; no LLM is responsible for choosing the next workflow stage.

## Install / refresh

Use the Python-coordinator installer from the AutoDev checkout:

```text
python -m automation.opencode_install --target-repo <TARGET_REPOSITORY> --python <PYTHON_LAUNCHER>
```

Linux example:

```bash
python3 -m automation.opencode_install \
  --target-repo ~/repos/TATATORPLAG \
  --python python3
```

The existing PowerShell convenience installer delegates to the same module.

The installer first installs the normal AutoDev OpenCode assets, then renders the configured Python launcher into the canonical `/autodev-issue-to-pr` and `/autodev-resume` command templates. The legacy `automation.opencode_adapter install` entry point remains available for compatibility/manual role workflows, but it does not replace those two commands with the deterministic coordinator templates.

## Run

Inside OpenCode:

```text
/autodev-issue-to-pr 29
```

or:

```text
/autodev-resume
```

The custom command's shell-output boundary starts `.opencode/autodev.py coordinate`. Python then owns preflight/prepare, durable resume selection, role preparation, role acceptance, verification stages, bounded repair counters, PR/CI stages, and terminal state.

For direct debugging without the TUI command wrapper, run from the target repository:

```text
python .opencode/autodev.py coordinate --arguments "29"
python .opencode/autodev.py coordinate --resume
```

Use the exact launcher installed in `.opencode/autodev.json`.

## Role execution

For each model-heavy role Python performs:

```text
prepare role artifact
  -> opencode run --agent autodev-<role> --dir <repo> --format json <bounded instruction>
  -> Python validates/accepts the role output
  -> durable accepted-artifact/hash check
  -> next deterministic transition
```

The child model does not own `prepare`, `accept`, `role-check`, next-role selection, repair counters, or resume interpretation in this mode. A process exit of zero without a valid accepted artifact still fails closed. If protocol validation rejects a role output, Python permits the existing single contract-correction attempt and validates again.

Configured `agent.autodev-<role>.model` mappings remain the model-routing source of truth. The Python coordinator chooses only the named AutoDev agent and does not hardcode a provider or model.

## Compatibility

Standalone `/autodev-read`, `/autodev-plan`, `/autodev-implement`, `/autodev-fix`, and `/autodev-verify` remain available. In those standalone/manual invocations the role agent continues to use its existing prepare/accept contract itself.

The checked-in `autodev-coordinator` agent remains as a legacy/manual compatibility asset, but the canonical Python-coordinator-installed issue-to-PR and resume commands do not target it.

AutoDev never merges the pull request automatically.
