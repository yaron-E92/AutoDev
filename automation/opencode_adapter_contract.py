from __future__ import annotations

import re
from pathlib import Path
from automation import workflow_stages
from automation.prompt_runner import (
    REQUIRED_PLAN_HEADINGS,
    PromptRunnerError,
    handle_planner_output,
)


AUTODEV_ROOT = Path(__file__).resolve().parents[1]

CURRENT_DIR = Path(".autodev-run") / "current"

COMMAND_FILES = (
    "autodev-issue-to-pr.md",
    "autodev-status.md",
    "autodev-resume.md",
    "autodev-read.md",
    "autodev-plan.md",
    "autodev-implement.md",
    "autodev-fix.md",
    "autodev-verify.md",
)

AGENT_FILES = (
    "autodev-coordinator.md",
    "autodev-reader.md",
    "autodev-synthesizer.md",
    "autodev-planner.md",
    "autodev-implementer.md",
    "autodev-fixer.md",
    "autodev-verifier.md",
)

ROLE_NAMES = ("reader", "synthesizer", "planner", "implementer", "fixer", "verifier")

OPENCODE_ROLE_NAMES = ("coordinator", *ROLE_NAMES)

AUTODEV_AGENT_BY_ROLE = {role: f"autodev-{role}" for role in OPENCODE_ROLE_NAMES}

COORDINATOR_STAGES = workflow_stages.STAGES

MAX_HANDOFF_CHARS = 30_000

MAX_READER_BUNDLE_CHARS = 24_000

OPENCODE_PROTOCOL_VERSION = 1

DEFAULT_MAX_REPAIR_ATTEMPTS = workflow_stages.DEFAULT_MAX_REPAIR_ATTEMPTS

DEFAULT_MAX_SEMANTIC_REPAIR_ATTEMPTS = workflow_stages.DEFAULT_MAX_SEMANTIC_REPAIR_ATTEMPTS

_UNSUPPORTED_MODEL_OVERRIDE = re.compile(
    r"(?<!\S)--(?:model|role-model-profile)(?=$|[\s=])"
)

class OpenCodeAdapterError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        classification: str = workflow_stages.FAILURE_DETERMINISTIC,
    ) -> None:
        super().__init__(message)
        self.classification = classification

def role_contracts() -> dict[str, dict[str, object]]:
    return {
        "reader": {
            "input_artifact": ".autodev-run/current/reader.md",
            "output_artifact": ".autodev-run/current/reader-brief.md",
            "format": "bounded text/Markdown handoff",
            "max_chars": MAX_HANDOFF_CHARS,
            "prepare": "python .opencode/autodev.py prepare --role reader",
            "accept": "python .opencode/autodev.py accept --role reader --input .autodev-run/current/reader-brief.md",
        },
        "synthesizer": {
            "input_artifact": ".autodev-run/current/synthesizer.md",
            "output_artifact": ".autodev-run/current/synthesized-handoff.md",
            "format": "bounded text/Markdown cross-area handoff",
            "max_chars": MAX_HANDOFF_CHARS,
            "prepare": "python .opencode/autodev.py prepare --role synthesizer",
            "accept": "python .opencode/autodev.py accept --role synthesizer --input .autodev-run/current/synthesized-handoff.md",
        },
        "planner": {
            "input_artifact": ".autodev-run/current/planner.md",
            "template_artifact": ".autodev-run/current/plan.template.md",
            "output_artifact": ".autodev-run/current/plan.md",
            "format": "exact six-section AutoDev plan",
            "required_sections": list(REQUIRED_PLAN_HEADINGS),
            "max_chars": MAX_HANDOFF_CHARS,
            "prepare": "python .opencode/autodev.py prepare --role planner",
            "accept": "python .opencode/autodev.py accept --role planner --input .autodev-run/current/plan.md",
        },
        "implementer": {
            "input_artifact": ".autodev-run/current/implementer.md",
            "output_artifact": ".autodev-run/current/commit-message.txt",
            "format": "one non-empty commit-message line, maximum 200 characters",
            "max_chars": 200,
            "prepare": "python .opencode/autodev.py prepare --role implementer",
            "coordinator_prepare_required": False,
            "coordinator_input_note": "stage --name render-implementer already rendered implementer.md",
            "accept": "python .opencode/autodev.py accept --role implementer",
        },
        "fixer": {
            "input_artifact": "one repair artifact selected by the explicit prepare command",
            "output_artifact": "target repository edits only",
            "format": "targeted source repair; no new AutoDev protocol artifact",
            "prepare": [
                "python .opencode/autodev.py prepare --role fixer --arguments local",
                "python .opencode/autodev.py prepare --role fixer --arguments semantic",
                "python .opencode/autodev.py prepare --role fixer --arguments ci",
            ],
            "accept": "python .opencode/autodev.py accept --role fixer",
        },
        "verifier": {
            "input_artifact": ".autodev-run/current/verifier.md",
            "template_artifact": ".autodev-run/current/verification-result.template.json",
            "output_artifact": ".autodev-run/current/verification-result.json",
            "format": "strict semantic JSON using only parser-supported fields/enums and exact pre-populated acceptance criteria",
            "prepare": "python .opencode/autodev.py prepare --role verifier",
            "accept": "python .opencode/autodev.py accept --role verifier --input .autodev-run/current/verification-result.json",
        },
    }
