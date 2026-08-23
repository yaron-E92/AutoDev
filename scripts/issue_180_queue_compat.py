from __future__ import annotations

from pathlib import Path


PATH = Path("automation/queue_selection.py")
MARKER = "class _IssueQueueCompat:\n"


def main() -> None:
    text = PATH.read_text(encoding="utf-8")
    if MARKER in text:
        print("queue-selection compatibility proxy already installed")
        return

    anchor = "from automation.queue_workflow import inspect_queue, reconcile_queue\n"
    if anchor not in text:
        raise SystemExit("queue-selection direct queue-layer imports are missing")

    replacements = {
        "_run_gh(": "issue_queue._run_gh(",
        "_json_result(": "issue_queue._json_result(",
        "inspect_queue(": "issue_queue.inspect_queue(",
        "reconcile_queue(": "issue_queue.reconcile_queue(",
    }
    for before, after in replacements.items():
        text = text.replace(before, after)

    proxy = '''\n\nclass _IssueQueueCompat:\n    """Legacy monkeypatch surface backed by the new queue layers, not the facade module."""\n\n    DEFAULT_LIMIT = DEFAULT_LIMIT\n    QueueError = QueueError\n    QueueIssue = QueueIssue\n    QueueState = QueueState\n    _run_gh = staticmethod(_run_gh)\n    _json_result = staticmethod(_json_result)\n    inspect_queue = staticmethod(inspect_queue)\n    reconcile_queue = staticmethod(reconcile_queue)\n\n\nissue_queue = _IssueQueueCompat()\n'''
    text = text.replace(anchor, anchor + proxy, 1)
    PATH.write_text(text, encoding="utf-8")
    print("installed queue-selection compatibility proxy without importing issue_queue")


if __name__ == "__main__":
    main()
