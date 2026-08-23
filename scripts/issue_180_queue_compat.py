from __future__ import annotations

import re
from pathlib import Path


PATH = Path("automation/queue_selection.py")
MARKER = "class _IssueQueueCompat:\n"


def main() -> None:
    text = PATH.read_text(encoding="utf-8")

    anchor = "from automation.queue_workflow import inspect_queue, reconcile_queue\n"
    if anchor not in text:
        raise SystemExit("queue-selection direct queue-layer imports are missing")

    if MARKER not in text:
        proxy = '''\n\nclass _IssueQueueCompat:\n    """Legacy monkeypatch surface backed by the new queue layers, not the facade module."""\n\n    DEFAULT_LIMIT = DEFAULT_LIMIT\n    QueueError = QueueError\n    QueueIssue = QueueIssue\n    QueueState = QueueState\n    _run_gh = staticmethod(_run_gh)\n    _json_result = staticmethod(_json_result)\n    inspect_queue = staticmethod(inspect_queue)\n    reconcile_queue = staticmethod(reconcile_queue)\n\n\nissue_queue = _IssueQueueCompat()\n'''
        text = text.replace(anchor, anchor + proxy, 1)

    for name in ("_run_gh", "_json_result", "inspect_queue", "reconcile_queue"):
        text = re.sub(rf"(?<![\w.]){re.escape(name)}\(", f"issue_queue.{name}(", text)

    PATH.write_text(text, encoding="utf-8")
    print("ensured queue-selection compatibility proxy remains active without importing issue_queue")


if __name__ == "__main__":
    main()
