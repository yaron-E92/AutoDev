# Create Issues From Description

AutoDev includes a cross-platform helper for turning rough task ideas into structured GitHub issues.

The shared logic lives in:

```text
automation/create_issues_from_description.py
```

Use the OS-specific wrappers for normal use:

```text
linux/scripts/create-issues-from-description.sh
windows/scripts/create-issues-from-description.ps1
```

## Linux Usage

Run from the repository root:

```bash
linux/scripts/create-issues-from-description.sh \
  --description "Add dry-run mode to AutoDev issue creation" \
  --repo owner/AutoDev
```

Create issues after reviewing the dry-run output:

```bash
linux/scripts/create-issues-from-description.sh \
  --description-file ideas.md \
  --repo owner/AutoDev \
  --create \
  --yes
```

## Windows Usage

Run from the repository root:

```powershell
windows\scripts\create-issues-from-description.ps1 `
  --description "Add dry-run mode to AutoDev issue creation" `
  --repo owner/AutoDev
```

Create issues after reviewing the dry-run output:

```powershell
windows\scripts\create-issues-from-description.ps1 `
  --description-file ideas.md `
  --repo owner/AutoDev `
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

Each parsed description becomes one proposed issue.

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
- More than `--max-issues` descriptions are refused unless `--yes` confirms creation.
- Re-running the same description with the same creation log skips duplicate creation.
- The wrappers fail clearly if `python` or `gh` is missing.
