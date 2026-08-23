from __future__ import annotations

import json


def write_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")

def write_json(path, value):
    write_text(path, json.dumps(value, indent=2, sort_keys=True) + "\n")

def write_executable_text(path, text):
    write_text(path, text)
    path.chmod(path.stat().st_mode | 0o755)
