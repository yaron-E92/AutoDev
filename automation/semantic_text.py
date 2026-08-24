from __future__ import annotations

import hashlib
import re

from automation.semantic_contract import (
    SemanticVerifierError,
    _TEMPLATE_PLACEHOLDER,
)

def render_template(template: str, values: dict[str, str]) -> str:
    unresolved: set[str] = set()
    for match in _TEMPLATE_PLACEHOLDER.finditer(template):
        key = match.group("new") or match.group("legacy")
        if key not in values:
            unresolved.add(match.group(0))
    if unresolved:
        raise SemanticVerifierError(
            "semantic verifier prompt contains unresolved placeholders: "
            + ", ".join(sorted(unresolved)),
            classification="unresolved_semantic_placeholders",
        )

    def replacement(match: re.Match[str]) -> str:
        key = match.group("new") or match.group("legacy")
        return values.get(key, match.group(0))

    return _TEMPLATE_PLACEHOLDER.sub(replacement, template)

def _bounded(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    digest = hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()
    return value[:limit] + f"\n[truncated; sha256={digest}]\n"
