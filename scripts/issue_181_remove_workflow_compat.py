from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    path = ROOT / "automation/workflow_stages.py"
    text = path.read_text(encoding="utf-8")
    text = text.replace("import functools\n", "")
    text = text.replace("import inspect\n", "")
    start_marker = "# The pre-refactor module was deliberately monkeypatch-friendly:"
    end_marker = "# Explicitly install the cross-cutting compatibility boundaries in the modules\n"
    start = text.find(start_marker)
    end = text.find(end_marker, start)
    if start < 0 or end < 0:
        raise SystemExit("workflow compatibility adapter markers missing")
    text = text[:start] + end_marker + text[end + len(end_marker):]
    path.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
