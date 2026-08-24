from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_required(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"missing expected text in {path}: {old!r}")
    path.write_text(text.replace(old, new), encoding="utf-8")


def main() -> None:
    path = ROOT / "automation/context_optimization.py"
    text = path.read_text(encoding="utf-8")
    text = text.replace(
        "4. `.autodev-run/current/recommended-command-groups.json` — consult only for verification scope.\n"
        "5. `.autodev-run/current/coder-plan.md` — normally DO NOT read. It is a prior planning artifact and is redundant with your role. Read it only if the synthesized handoff explicitly says synthesis is unavailable/fallback or a critical ambiguity remains.\n",
        "4. `.autodev-run/current/recommended-command-groups.json` — consult only for verification scope.\n",
    )
    text = text.replace(
        "2. `.autodev-run/current/plan.md` — authoritative implementation constraints; if absent, use `.autodev-run/current/coder-plan.md`.\n",
        "2. `.autodev-run/current/plan.md` — authoritative implementation constraints.\n",
    )
    text = text.replace(
        '            ("recommended-command-groups.json", False, "verification scope"),\n'
        '            ("coder-plan.md", False, "fallback-only prior plan"),\n',
        '            ("recommended-command-groups.json", False, "verification scope"),\n',
    )
    text = text.replace(
        '        plan_name = "plan.md" if (current / "plan.md").is_file() else "coder-plan.md"\n'
        '        specs = [\n'
        '            ("issue.md", True, "requirements"),\n'
        '            (plan_name, True, "implementation constraints"),\n',
        '        specs = [\n'
        '            ("issue.md", True, "requirements"),\n'
        '            ("plan.md", True, "implementation constraints"),\n',
    )
    path.write_text(text, encoding="utf-8")

    path = ROOT / "automation/opencode_adapter_handoff.py"
    text = path.read_text(encoding="utf-8")
    text = text.replace(
        '    return _read_text(current / "plan.md") or _read_text(current / "coder-plan.md")\n',
        '    return _read_text(current / "plan.md")\n',
    )
    text = text.replace(
        '    coder_plan = sanitize_model_output(_read_text(current / "coder-plan.md"))\n',
        '',
    )
    text = text.replace(
        'Area-reader coder / implementation plan:\n{coder_plan}\n\n',
        '',
    )
    path.write_text(text, encoding="utf-8")

    path = ROOT / "tests/test_context_optimization.py"
    text = path.read_text(encoding="utf-8")
    text = text.replace('            (current / "coder-plan.md").write_text("prior plan\\n", encoding="utf-8")\n', '')
    text = text.replace('            self.assertIn("normally DO NOT read", optimized)\n', '')
    text = text.replace('            self.assertFalse(by_name["coder-plan.md"]["required"])\n', '            self.assertNotIn("coder-plan.md", by_name)\n')
    path.write_text(text, encoding="utf-8")

    path = ROOT / "tests/test_opencode_integration.py"
    text = path.read_text(encoding="utf-8")
    text = text.replace('            (current / "coder-plan.md").write_text("Reader plan\\n", encoding="utf-8")\n', '')
    path.write_text(text, encoding="utf-8")

    path = ROOT / "tests/test_run_manifest.py"
    text = path.read_text(encoding="utf-8")
    text = text.replace('plan = self._artifact(root, "coder-plan.md", "original plan")', 'plan = self._artifact(root, "plan.md", "original plan")')
    text = text.replace('self.assertIn("coder-plan.md", problems[0])', 'self.assertIn("plan.md", problems[0])')
    path.write_text(text, encoding="utf-8")

    path = ROOT / "docs/opencode-context-sizing.md"
    text = path.read_text(encoding="utf-8")
    text = text.replace(
        '- **Planner** reads `issue.md` and `synthesized-handoff.md` by default. `coder-plan.md` is explicitly fallback-only because asking a Planner to ingest an earlier planner output is normally redundant. Facts, command groups, and the workspace snapshot are consulted only to validate a concrete path/fact.\n',
        '- **Planner** reads `issue.md` and `synthesized-handoff.md` by default. Facts, command groups, and the workspace snapshot are consulted only to validate a concrete path/fact.\n',
    )
    path.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
