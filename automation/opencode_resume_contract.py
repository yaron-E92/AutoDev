from __future__ import annotations

from pathlib import Path
from automation import run_manifest, workflow_stages


ROLE_NAMES = ("reader", "synthesizer", "planner", "implementer", "fixer", "verifier")

MODEL_STAGE_ROLE = {
    "repository-read": "reader",
    "handoff-synthesized": "synthesizer",
    "plan-created": "planner",
    "implementation-generated": "implementer",
    "semantic-verified": "verifier",
}

REPAIR_STAGE_KIND = {
    "deterministic-verified": "local",
    "semantic-verified": "semantic",
    "pr-created": "ci",
}

NEXT_ACTION = {
    "issue-selected": "prepare",
    "repository-read": "reader",
    "handoff-synthesized": "synthesizer",
    "plan-created": "planner",
    "implementation-generated": "implementer",
    "patch-applied": "implementer-checkpoint",
    "deterministic-verified": "local-check",
    "semantic-verified": "verifier",
    "pr-created": "pr-and-ci",
}

class OpenCodeResumeError(ValueError):
    pass

def manifest_path(repo: Path) -> Path:
    return repo.expanduser().resolve() / workflow_stages.CURRENT_DIR / run_manifest.MANIFEST_NAME

def has_manifest(repo: Path) -> bool:
    return manifest_path(repo).is_file()
