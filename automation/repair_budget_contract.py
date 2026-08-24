from __future__ import annotations



FAILURE_REPAIR_BUDGET_EXHAUSTED = "repair-budget-exhausted"

ROOT_FAILURE_CLASSIFICATION = "code-repairable"

FORMULA_VERSION = 1

POLICY_ENV = "SEMANTIC_REPAIR_BUDGET_POLICY"

FIXED_LIMIT_ENV = "MAX_SEMANTIC_REPAIR_ATTEMPTS"

ADAPTIVE_MIN_ENV = "SEMANTIC_REPAIR_ADAPTIVE_MIN_ATTEMPTS"

ADAPTIVE_MAX_ENV = "SEMANTIC_REPAIR_ADAPTIVE_MAX_ATTEMPTS"

ADAPTIVE_BASE_ENV = "SEMANTIC_REPAIR_ADAPTIVE_BASE_ATTEMPTS"

LINES_PER_ATTEMPT_ENV = "SEMANTIC_REPAIR_LINES_PER_ATTEMPT"

DEFAULT_ADAPTIVE_MIN = 1

DEFAULT_ADAPTIVE_MAX = 5

DEFAULT_ADAPTIVE_BASE = 1

DEFAULT_LINES_PER_ATTEMPT = 200

_GENERATED_PREFIXES = (
    ".git/",
    ".autodev-run/",
    "bin/",
    "obj/",
    "node_modules/",
    "dist/",
    "build/",
    "coverage/",
    ".vs/",
    ".idea/",
    ".vscode/",
    ".venv/",
    "venv/",
    "__pycache__/",
)

_BINARY_SUFFIXES = {
    ".7z",
    ".a",
    ".avi",
    ".bin",
    ".bmp",
    ".class",
    ".dll",
    ".dylib",
    ".eot",
    ".exe",
    ".gif",
    ".gz",
    ".ico",
    ".jar",
    ".jpeg",
    ".jpg",
    ".lib",
    ".mov",
    ".mp3",
    ".mp4",
    ".o",
    ".obj",
    ".otf",
    ".pdf",
    ".png",
    ".pyc",
    ".so",
    ".tar",
    ".ttf",
    ".wav",
    ".webm",
    ".webp",
    ".woff",
    ".woff2",
    ".zip",
}

class SemanticRepairBudgetError(ValueError):
    pass
