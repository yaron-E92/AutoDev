from pathlib import Path

path = Path(__file__).resolve().with_name("issue_180_remove_legacy_runner.py")
text = path.read_text(encoding="utf-8")
old = '''    if old not in text:\n        raise SystemExit("legacy issue-runner CI smoke block not found")\n    path.write_text(text.replace(old, new), encoding="utf-8")\n'''
new = '''    if old not in text:\n        if new in text:\n            return\n        raise SystemExit("neither legacy nor canonical CI smoke block found")\n    path.write_text(text.replace(old, new), encoding="utf-8")\n'''
if old not in text and new not in text:
    raise SystemExit("patch_ci implementation not found")
path.write_text(text.replace(old, new), encoding="utf-8")
