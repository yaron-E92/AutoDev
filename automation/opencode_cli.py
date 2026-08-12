from __future__ import annotations

import shutil
from typing import Callable


class OpenCodeCliError(RuntimeError):
    pass


def resolve_opencode_cli(*, which: Callable[[str], str | None] | None = None) -> str:
    resolver = which or shutil.which
    resolved = resolver("opencode")
    if not resolved:
        raise OpenCodeCliError("OpenCode CLI was not found on PATH")
    return str(resolved)
