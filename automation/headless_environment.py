from __future__ import annotations

import os
from typing import Mapping


def environment(base: Mapping[str, str] | None = None) -> dict[str, str]:
    result = dict(base or os.environ)
    result["GIT_TERMINAL_PROMPT"] = "0"
    result["GCM_INTERACTIVE"] = "Never"
    result["GH_PROMPT_DISABLED"] = "1"
    ssh = str(result.get("GIT_SSH_COMMAND", "") or "").strip()
    if "batchmode" not in ssh.casefold():
        result["GIT_SSH_COMMAND"] = (ssh + " -o BatchMode=yes").strip() if ssh else "ssh -o BatchMode=yes"
    return result
