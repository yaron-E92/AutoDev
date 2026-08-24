from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    headroom_path = ROOT / "automation" / "headroom.py"
    text = headroom_path.read_text(encoding="utf-8")
    old = '''    elif role == "verifier" and "Semantic JSON contract:" in prompt:\n        pairs = [\n            ("Implementation plan:\\n", "\\n\\nCurrent implementation diff or summary:"),\n            ("Current implementation diff or summary:\\n", "\\n\\nSemantic-only evidence:"),\n            ("Synthesized repository handoff:\\n", "\\n\\nChanged files:"),\n            ("Changed files:\\n", "\\n\\nDeterministic verification evidence:"),\n            ("Deterministic verification evidence:\\n", "\\n\\nRelevant uncertainty or skipped-check notes:"),\n            ("Relevant uncertainty or skipped-check notes:\\n", "\\n\\nSemantic JSON contract:"),\n        ]\n'''
    new = '''    elif role == "verifier" and "Output contract:" in prompt:\n        pairs = [\n            ("Synthesized repository handoff:\\n", "\\n\\nImplementation plan:"),\n            ("Implementation plan:\\n", "\\n\\nChanged files:"),\n            ("Changed files:\\n", "\\n\\nCurrent diff:"),\n            ("Current diff:\\n", "\\n\\nDeterministic verification evidence:"),\n            ("Deterministic verification evidence:\\n", "\\n\\nCross-file regression evidence:"),\n            ("Cross-file regression evidence:\\n", "\\n\\nRelevant uncertainty or skipped-check notes:"),\n            ("Relevant uncertainty or skipped-check notes:\\n", "\\n\\nOutput contract:"),\n        ]\n'''
    if old not in text:
        raise SystemExit("retired verifier Headroom marker block not found")
    text = text.replace(old, new, 1)
    text = text.replace('"{{RepairBrief}}" not in prompt', '"{~{RepairBrief}~}" not in prompt')
    text = text.replace('"{{ChangedFiles}}" not in prompt', '"{~{ChangedFiles}~}" not in prompt')
    text = text.replace('"{{Diff}}" not in prompt', '"{~{Diff}~}" not in prompt')
    headroom_path.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
