# Create Issues From Description

AutoDev includes a cross-platform helper for turning rough task ideas into structured GitHub issues. The helper sends the rough description to a configured local Ollama model, expects structured JSON issue proposals back, then dry-runs or creates those issues with `gh`.

The shared logic lives in:

```text
automation/create_issues_from_description.py
```

Use the OS-specific wrappers for normal use:

```text
linux/scripts/create-issues-from-description.sh
windows/scripts/create-issues-from-description.ps1
```

## Model Setup

The tool uses Ollama's chat API by default:

```text
http://localhost:11434/api/chat
```

The default model is `devstral-small2-12k`, which is intended as the coder model in this repository's local model workflow. Override it with `--model` or `AUTODEV_ISSUE_MODEL`:

```bash
export AUTODEV_ISSUE_MODEL=devstral-small2-12k
```

You can also override the API URL:

```bash
export AUTODEV_OLLAMA_URL=http://localhost:11434/api/chat
```

The model may return one or more issue proposals for one rough description when the description clearly contains independent tasks.

## Linux Usage

Run from the repository root:

```bash
linux/scripts/create-issues-from-description.sh \
  --description "Add dry-run mode to AutoDev issue creation" \
  --repo owner/AutoDev \
  --model devstral-small2-12k
```

Create issues after reviewing the dry-run output:

```bash
linux/scripts/create-issues-from-description.sh \
  --description-file ideas.md \
  --repo owner/AutoDev \
  --model devstral-small2-12k \
  --create \
  --yes
```

## Windows Usage

Run from the repository root:

```powershell
windows\scripts\create-issues-from-description.ps1 `
  --description "Add dry-run mode to AutoDev issue creation" `
  --repo owner/AutoDev `
  --model devstral-small2-12k
```

Create issues after reviewing the dry-run output:

```powershell
windows\scripts\create-issues-from-description.ps1 `
  --description-file ideas.md `
  --repo owner/AutoDev `
  --model devstral-small2-12k `
  --create `
  --yes
```

## Explicit Repo Mode

Use `--repo <owner/name>` when you know the target repository:

```bash
linux/scripts/create-issues-from-description.sh \
  --description "Document AutoDev recovery commands" \
  --repo owner/AutoDev
```

## Repo-Map Inferred Mode

Use `--repo-map` when rough descriptions mention products or repositories by name:

```json
{
  "phoodab": "owner/PHOODAB",
  "survival garden": "owner/SurvivalGarden",
  "shuffle task": "owner/ShuffleTask",
  "autodev": "owner/AutoDev"
}
```

Then run:

```bash
linux/scripts/create-issues-from-description.sh \
  --description "Improve AutoDev issue repair docs" \
  --repo-map repo-map.json
```

If more than one repository matches, the tool refuses to create anything and prints the candidate repositories. Pass `--repo <owner/name>` to resolve the ambiguity.

## Single Description Mode

Pass one or more descriptions directly:

```bash
linux/scripts/create-issues-from-description.sh \
  --description "Add a local verification smoke check to AutoDev" \
  --repo owner/AutoDev
```

## Multi-Description File Mode

Use `--description-file` for several issue ideas. Separate ideas with `---` or Markdown headings:

```markdown
## First issue
Add retry handling to the issue creation wrapper.

---

## Second issue
Document the repo-map format.
```

Each parsed description is sent to the model. The model can return one issue or multiple issue proposals for each parsed description.

## Dry-Run Mode

Dry-run is the default. It prints:

- selected repository
- proposed title
- proposed body
- proposed labels
- exact `gh issue create` command that would run

You can pass `--dry-run` explicitly, but it is not required.

## Create Mode

Pass `--create --yes` to create issues with `gh issue create`:

```bash
linux/scripts/create-issues-from-description.sh \
  --description-file ideas.md \
  --repo owner/AutoDev \
  --create \
  --yes
```

Created issues are logged to `.codex-run/issue-creation-log.jsonl` by default. Override that path with `--creation-log`.

## Safety Behavior

- The default mode never creates issues.
- `--create` requires `--yes` for non-interactive creation.
- Empty and near-empty descriptions are refused.
- Ambiguous repository matches are refused.
- More than `--max-issues` issue proposals are refused unless `--yes` confirms creation.
- Re-running the same description/title pair with the same creation log skips duplicate creation.
- The wrappers fail clearly if `python` or `gh` is missing.