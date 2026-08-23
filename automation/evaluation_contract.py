from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_CASES = REPO_ROOT / "benchmarks" / "eval" / "cases.json"

DEFAULT_PROFILES = REPO_ROOT / "benchmarks" / "eval" / "profiles.json"

DEFAULT_RESULTS_ROOT = REPO_ROOT / ".benchmark-results"

SCHEMA_VERSION = 1

UNKNOWN = "unknown"

DEPENDENCY_NAMES = {
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "requirements.txt",
    "pyproject.toml",
    "poetry.lock",
    "Pipfile",
    "Pipfile.lock",
    "Directory.Packages.props",
    "packages.lock.json",
    "Cargo.toml",
    "Cargo.lock",
    "go.mod",
    "go.sum",
}

class EvalError(ValueError):
    pass

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
