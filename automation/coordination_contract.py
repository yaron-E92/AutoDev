from __future__ import annotations


ROLE_PROMPT = (
    "AutoDev Python has already prepared the current {role} role. "
    "Follow the installed autodev-{role} contract for the model-heavy work only: "
    "read the prepared .autodev-run/current artifacts, perform the requested reasoning or edits, "
    "and write only the contract output artifact. Do not run AutoDev prepare or accept commands; "
    "Python will validate and accept the result after this process exits. "
    "Return only success/failure and the output artifact path."
)
CORRECTION_PROMPT = (
    "AutoDev rejected the current {role} output once. Read "
    ".autodev-run/current/contract-correction-{role}.md, correct only the designated output artifact, "
    "and stop. Do not run AutoDev prepare or accept commands; Python will perform the final validation."
)
REPAIR_KINDS = {"fixer-local": "local", "fixer-semantic": "semantic", "fixer-ci": "ci"}
ROLE_ACTIONS = {"reader", "synthesizer", "planner"}
ROLE_TIMEOUT_SECONDS = {
    "reader": 600,
    "synthesizer": 900,
    "planner": 900,
    "implementer": 1800,
    "fixer": 900,
    "verifier": 900,
}
MAX_TRANSITIONS = 100
