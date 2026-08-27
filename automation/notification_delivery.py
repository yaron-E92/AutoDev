from __future__ import annotations

import os
import shutil
import subprocess
from typing import Callable

from automation.notification_contract import (
    NOTIFICATION_NATIVE,
    NotificationResult,
)


def native_notify(
    title: str,
    message: str,
    *,
    runner: Callable[..., object] = subprocess.run,
    which: Callable[[str], str | None] = shutil.which,
    platform_name: str | None = None,
) -> NotificationResult:
    platform = (platform_name or ("windows" if os.name == "nt" else "posix")).casefold()
    if platform == "windows":
        executable = which("msg") or which("msg.exe")
        if not executable:
            return NotificationResult(True, False, NOTIFICATION_NATIVE, "msg.exe is unavailable")
        argv = [executable, "*", "/TIME:10", f"{title}: {message}"]
    else:
        executable = which("notify-send")
        if not executable:
            return NotificationResult(True, False, NOTIFICATION_NATIVE, "notify-send is unavailable")
        argv = [executable, title, message]
    try:
        completed = runner(
            argv,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
        )
    except OSError:
        return NotificationResult(
            True,
            False,
            NOTIFICATION_NATIVE,
            "native notifier could not be launched",
        )
    if int(getattr(completed, "returncode", 1)) != 0:
        return NotificationResult(
            True,
            False,
            NOTIFICATION_NATIVE,
            "native notifier returned a nonzero exit code",
        )
    return NotificationResult(True, True, NOTIFICATION_NATIVE)
