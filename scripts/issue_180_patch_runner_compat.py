from __future__ import annotations

from pathlib import Path


PATH = Path("automation/run_real_issue.py")


def main() -> None:
    text = PATH.read_text(encoding="utf-8")
    old = '            if name in wrapped or name.startswith("__") or name not in facade:\n                continue\n'
    new = (
        '            if (\n'
        '                name in wrapped\n'
        '                or name.startswith("__")\n'
        '                or name.startswith("_compat")\n'
        '                or name == "_sync_compat_overrides"\n'
        '                or name not in facade\n'
        '            ):\n'
        '                continue\n'
    )
    # The generated public and core facades contain this pattern. Patch only the
    # public facade; the core facade has no facade module in its compatibility
    # module list and is not susceptible to self-wrapping.
    if text.count(old) != 1:
        raise SystemExit("unexpected generated runner compatibility layout")
    PATH.write_text(text.replace(old, new, 1), encoding="utf-8")


if __name__ == "__main__":
    main()
