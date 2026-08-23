from __future__ import annotations

from dataclasses import asdict, dataclass

from automation.queue_contract import (
    ATTENTION_LABEL,
    MANAGED_LABEL,
    QueueState,
)

def queue_summary(states: list[QueueState]) -> dict[str, int]:
    open_managed = [
        state
        for state in states
        if state.issue.state == "open" and MANAGED_LABEL in state.issue.labels
    ]
    return {
        "managed": len(open_managed),
        "ready": sum(state.reason == "ready" for state in open_managed),
        "dependency_blocked": sum(
            state.reason == "blocked" for state in open_managed
        ),
        "attention_required": sum(
            ATTENTION_LABEL in state.issue.labels for state in open_managed
        ),
        "running": sum(state.reason == "running" for state in open_managed),
        "policy_excluded": sum(
            state.reason == "policy-excluded" for state in open_managed
        ),
    }

def explain_state(state: QueueState) -> str:
    number = state.issue.number
    if state.reason == "blocked":
        blockers = ", ".join(
            f"#{item.number} {item.title}".strip() for item in state.open_blockers
        )
        return f"#{number} blocked by: {blockers}"
    explanations = {
        "ready": "managed, open, dependency-free, and eligible for autonomous execution",
        "attention": "requires human attention",
        "running": "already has an active AutoDev claim/run",
        "policy-excluded": "repository policy disables autonomous execution",
        "unmanaged": "not authorized for autonomous AutoDev work",
        "closed": "issue is closed",
    }
    return f"#{number} {state.reason}: {explanations.get(state.reason, state.reason)}"

def _state_json(state: QueueState) -> dict[str, object]:
    value = asdict(state)
    value["issue"]["labels"] = list(state.issue.labels)  # type: ignore[index]
    return value
