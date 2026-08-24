from pathlib import Path

root = Path(__file__).resolve().parents[1]
path = root / "scripts" / "issue_180_remove_module_facades.py"
text = path.read_text(encoding="utf-8")
if "import re\n" not in text:
    text = text.replace("import ast\n", "import ast\nimport re\n", 1)
old = '''    for facade in FACADES:
        if (
            f"from automation import {facade}" in updated
            or f"automation.{facade}" in updated
            or f"{facade}." in updated
        ):
            raise SystemExit(
                f"facade reference remains in {path.relative_to(ROOT)}: {facade}"
            )
'''
new = '''    for facade in FACADES:
        escaped = re.escape(facade)
        if (
            re.search(rf"\\b{escaped}\\.", updated)
            or re.search(rf"from\\s+automation\\s+import[^\\n]*\\b{escaped}\\b", updated)
            or re.search(rf"from\\s+automation\\.{escaped}\\s+import\\b", updated)
        ):
            raise SystemExit(
                f"facade reference remains in {path.relative_to(ROOT)}: {facade}"
            )
'''
if old in text:
    text = text.replace(old, new)
elif new not in text:
    raise SystemExit("facade guard block not found")
path.write_text(text, encoding="utf-8")
