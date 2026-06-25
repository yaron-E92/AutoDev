# Real-Issue Runner

`automation/run_real_issue.py` is an operational runner for real GitHub issues. It uses the existing area-reader v2 planning flow, keeps concise run outputs by default, and applies the same Codex automation constraints: issue-scoped changes, a branch per issue, explicit verification, dirty-tree checks, and draft PRs only when requested.

This is not a benchmark script. The area-reader benchmark still produces raw prompts, model JSON, metrics, and large context bundles. The real-issue runner copies only the concise operational artifacts unless `--debug-artifacts` is passed.

## Required Tools

- Python 3
- GitHub CLI, authenticated with access to the target repository
- Git
- Ollama models reachable by the existing area-reader script

Default models:

```text
reader: qwen35-9b-32k
coder: devstral-small2-12k
```

## Linux Usage

```bash
scripts/run-real-issue.sh \
  --repo . \
  --github-repo owner/AutoDev \
  --issue 18 \
  --mode plan-only \
  --out .codex-run/real-issue-18
```

The shell wrapper runs from the repository root, checks for `python` and `gh`, and forwards all arguments to `automation/run_real_issue.py`.

## Windows Usage

```powershell
scripts\run-real-issue.ps1 `
  --repo . `
  --github-repo owner/AutoDev `
  --issue 18 `
  --mode plan-only `
  --out .codex-run\real-issue-18
```

The PowerShell wrapper does not require WSL. It runs from the repository root, checks for `python` and `gh`, and forwards all arguments to the Python entry point.

## Modes

- `plan-only`: fetches the issue, creates or validates the issue branch, runs area-reader planning, runs recommended verification, and writes concise outputs. It does not intentionally modify project files or open a PR.
- `implement`: does everything in `plan-only` and writes `implementation-prompt.md` from the existing automation prompt, synthesized handoff, and coder plan. It does not open a PR.
- `pr`: verifies, commits current issue-scoped changes, pushes the branch, and opens a draft PR with `gh pr create --draft`.

## Outputs

The `--out` directory contains:

- `issue.md`
- `routed-areas.json`
- `synthesized-handoff.md`
- `coder-plan.md`
- `recommended-command-groups.json`
- `verification-result-summary.md`
- `implementation-prompt.md` in `implement` and `pr` modes
- `final-pr-summary.md`

Pass `--debug-artifacts` to retain the raw area-reader directory under `area-reader-debug/`.

## Safety Behavior

The runner refuses to run with uncommitted changes unless `--allow-dirty` is passed. It creates a dedicated `codex/issue-<number>-...` branch and refuses to create a PR from `main` or `master`. It prints commands before running them, never merges PRs, and does not manually trigger remote CI.

## Relation To Existing Automation

The existing issue-to-PR automation remains the trusted path for Codex Desktop processing. This runner packages the same operating ideas into a local cross-platform command: fetch one issue, plan with area-reader v2, run recommended local verification, stop clearly by mode, and keep outputs concise enough for real operational use.
