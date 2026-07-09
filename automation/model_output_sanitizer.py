from __future__ import annotations

import re

ANSI_ESCAPE_RE = re.compile(
    r"(?:\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07]*(?:\x07|\x1b\\)|\x1b[@-Z\\-_])"
)
CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def sanitize_model_output(value: str, *, ensure_trailing_newline: bool = False) -> str:
    cleaned = ANSI_ESCAPE_RE.sub("", value)
    cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n")
    cleaned = CONTROL_RE.sub("", cleaned)
    cleaned = "\n".join(line.rstrip() for line in cleaned.split("\n")).strip()
    if cleaned and ensure_trailing_newline:
        return cleaned + "\n"
    return cleaned