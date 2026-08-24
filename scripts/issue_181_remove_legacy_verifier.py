from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

VERIFIER_TEMPLATE = '''Use the issue-to-pr-automation skill.

You are the independent Verifier for this repository.

Operating mode: COMPLETION CHECK ONLY — NO CODE CHANGES.

Strict rules:

- Do not edit files.
- Do not write a patch.
- Do not propose refactors, redesigns, or unrelated improvements.
- Judge only the original issue, acceptance criteria, supplied implementation evidence, and requested scope.
- Never approve or merge a pull request.

Original issue:
{~{IssueText}~}

Implementation plan:
{~{Plan}~}

Current implementation diff or summary:
{~{Diff}~}

Detectable acceptance criteria:
{~{AcceptanceCriteria}~}

Synthesized repository handoff:
{~{SynthesizedHandoff}~}

Changed files:
{~{ChangedFiles}~}

Deterministic verification evidence:
{~{DeterministicEvidence}~}

Cross-file regression evidence:
{~{CrossFileRegressionEvidence}~}

Relevant uncertainty or skipped-check notes:
{~{UncertaintyNotes}~}

Return JSON only. Do not use Markdown fences or commentary.

{
  "verdict": "pass | repair | blocked",
  "requirements": [
    {
      "criterion": "criterion text",
      "status": "met | missing | uncertain",
      "evidence": ["path, test, command, or supplied evidence"]
    }
  ],
  "findings": [
    {
      "severity": "blocking | warning",
      "message": "concise finding",
      "path": "optional relevant path"
    }
  ],
  "repair_brief": "targeted repair instruction, or an empty string"
}

- A `pass` verdict is valid only when every requirement is `met` and no finding is `blocking`.
- Explicitly check removed or changed public/cross-file symbols against references in unchanged files. A remaining unchanged reference is blocking unless supplied deterministic evidence proves it remains valid.
- Warnings alone do not block.
- Use `repair` only for a concrete issue that can be corrected with a targeted patch.
- Use `blocked` when required evidence or a human decision is missing, or the outcome cannot be safely verified.
'''


def remove_function(path: Path, start_marker: str, end_marker: str) -> None:
    text = path.read_text(encoding="utf-8")
    start = text.find(start_marker)
    if start < 0:
        return
    end = text.find(end_marker, start)
    if end < 0:
        raise SystemExit(f"cannot bound {start_marker.strip()} in {path}")
    path.write_text(text[:start] + text[end:], encoding="utf-8")


def edit_workflow_code() -> None:
    prompts = ROOT / "automation/workflow_prompts.py"
    remove_function(prompts, "def render_legacy_verifier(\n", "def commit_message(")

    verification = ROOT / "automation/workflow_verification.py"
    text = verification.read_text(encoding="utf-8")
    text = text.replace("    render_legacy_verifier,\n", "")
    text = text.replace(
        "    render_legacy_verifier(repo, current, state, autodev_root, runner=runner)\n"
        "    state[\"Status\"] = \"CiPassedVerifierPromptRendered\"\n",
        "    state[\"Status\"] = \"CiPassed\"\n",
    )
    verification.write_text(text, encoding="utf-8")

    hooks = ROOT / "automation/windows_workflow_hooks.py"
    text = hooks.read_text(encoding="utf-8")
    text = text.replace(
        "        core.render_legacy_verifier(repo, current, state, autodev_root, runner=runner)\n"
        "        state[\"Status\"] = \"CiPassedVerifierPromptRendered\"\n",
        "        state[\"Status\"] = \"CiPassed\"\n",
    )
    remove_function(
        hooks,
        "def install(core) -> None:\n",
        "",
    ) if False else None
    # This explicit mutation installer has no caller; build_execute_stage is the
    # canonical hook used by workflow_stages._ensure_policy_hooks.
    marker = "\ndef install(core) -> None:\n"
    start = text.find(marker)
    if start >= 0:
        text = text[:start].rstrip() + "\n"
    hooks.write_text(text, encoding="utf-8")

    stages = ROOT / "automation/workflow_stages.py"
    text = stages.read_text(encoding="utf-8")
    text = text.replace("    render_legacy_verifier,\n", "")
    stages.write_text(text, encoding="utf-8")


def edit_semantic_template_contract() -> None:
    (ROOT / "promptTemplates/verifier.md").write_text(VERIFIER_TEMPLATE, encoding="utf-8")

    contract = ROOT / "automation/semantic_contract.py"
    text = contract.read_text(encoding="utf-8")
    text = text.replace('\n_LEGACY_ONLY_PLACEHOLDERS = {"LocalCheck", "StackContext"}\n', "\n")
    contract.write_text(text, encoding="utf-8")

    semantic_text = ROOT / "automation/semantic_text.py"
    text = semantic_text.read_text(encoding="utf-8")
    text = text.replace(
        "    _LEGACY_ONLY_PLACEHOLDERS,\n",
        "",
    )
    text = text.replace(
        "        if key not in values and key not in _LEGACY_ONLY_PLACEHOLDERS:\n",
        "        if key not in values:\n",
    )
    semantic_text.write_text(text, encoding="utf-8")


def edit_tests() -> None:
    for path in (ROOT / "tests").glob("test_*.py"):
        text = path.read_text(encoding="utf-8")
        text = text.replace("CiPassedVerifierPromptRendered", "CiPassed")
        path.write_text(text, encoding="utf-8")

    path = ROOT / "tests/test_workflow_stages.py"
    text = path.read_text(encoding="utf-8")
    marker = "    def test_render_legacy_verifier_preserves_semantic_placeholders(self):\n"
    start = text.find(marker)
    if start >= 0:
        end = text.find("\n    def ", start + len(marker))
        if end < 0:
            end = text.find("\n\nif __name__", start)
        if end < 0:
            raise SystemExit("cannot bound legacy verifier workflow test")
        text = text[:start] + text[end:]
    path.write_text(text, encoding="utf-8")


def main() -> None:
    edit_workflow_code()
    edit_semantic_template_contract()
    edit_tests()


if __name__ == "__main__":
    main()
