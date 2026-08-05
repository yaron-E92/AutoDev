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
    cleaned = collapse_wrapped_duplicate_fragments(cleaned)
    cleaned = "\n".join(line.rstrip() for line in cleaned.split("\n")).strip()
    if cleaned and ensure_trailing_newline:
        return cleaned + "\n"
    return cleaned


def collapse_wrapped_duplicate_fragments(value: str) -> str:
    lines: list[str] = []
    for line in value.split("\n"):
        current = line.strip()
        if lines and current:
            previous = lines[-1].rstrip()
            previous_token = re.search(r"([A-Za-z][A-Za-z0-9_-]*)$", previous)
            current_token = re.match(r"([A-Za-z][A-Za-z0-9_-]*)(.*)$", current)
            if previous_token and current_token:
                previous_word = previous_token.group(1)
                current_word = current_token.group(1)
                if is_wrapped_duplicate_fragment(previous_word, current_word):
                    lines[-1] = previous[:previous_token.start(1)] + current_word + current_token.group(2)
                    continue
        lines.append(line)
    return "\n".join(lines)


def is_wrapped_duplicate_fragment(fragment: str, word: str) -> bool:
    fragment_key = fragment.casefold()
    word_key = word.casefold()
    return (
        len(fragment_key) < len(word_key)
        and len(fragment_key) <= 16
        and word_key.startswith(fragment_key)
        and word[len(fragment): len(fragment) + 1].isalpha()
    )
