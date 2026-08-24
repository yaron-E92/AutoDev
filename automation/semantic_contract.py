from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from automation.model_providers import ModelConfig, ModelProvider, ProviderError, load_provider_config


ALLOWED_VERDICTS = {"pass", "repair", "blocked"}

ALLOWED_REQUIREMENT_STATUSES = {"met", "missing", "uncertain"}

ALLOWED_FINDING_SEVERITIES = {"blocking", "warning"}

DEFAULT_MAX_SCHEMA_RETRIES = 1

DEFAULT_MAX_REPAIR_ATTEMPTS = 1

MAX_SCHEMA_RETRIES = 1

MAX_REPAIR_ATTEMPTS = 1

MAX_DIFF_CHARS = 120_000

MAX_EVIDENCE_CHARS = 30_000

MAX_REGRESSION_EVIDENCE_CHARS = 12_000

MAX_REGRESSION_SYMBOLS = 16

MAX_REGRESSION_REFERENCES = 24

MAX_REGRESSION_FILE_BYTES = 300_000

SEMANTIC_SOURCE_SUFFIXES = {
    ".cs",
    ".cshtml",
    ".razor",
    ".xaml",
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".java",
    ".kt",
    ".kts",
    ".go",
    ".rs",
    ".cpp",
    ".cc",
    ".c",
    ".h",
    ".hpp",
}

SEMANTIC_IGNORED_PARTS = {
    ".git",
    ".autodev-run",
    "bin",
    "obj",
    "node_modules",
    "dist",
    "build",
    "coverage",
    ".vs",
    ".idea",
    ".vscode",
    ".venv",
    "venv",
    "__pycache__",
}

_TEMPLATE_PLACEHOLDER = re.compile(
    r"\{~\{(?P<new>[A-Za-z][A-Za-z0-9_]*)\}~\}"
    r"|\{\{(?P<legacy>[A-Za-z][A-Za-z0-9_]*)\}\}"
)

_LEGACY_ONLY_PLACEHOLDERS = {"LocalCheck", "StackContext"}

_DECLARATION_PATTERNS = (
    re.compile(r"\b(?:class|interface|record|struct|enum|def|function|func)\s+([A-Za-z_][A-Za-z0-9_]*)"),
    re.compile(
        r"\b(?:public|protected|internal|export)\s+"
        r"(?:(?:static|virtual|override|abstract|sealed|async|readonly|const|partial|required|new)\s+)*"
        r"(?:[A-Za-z_][A-Za-z0-9_<>,?.\[\]]*\s+)+"
        r"([A-Za-z_][A-Za-z0-9_]*)\s*(?=\{|=>|\(|=|;)"
    ),
    re.compile(r"\b(?:const|let|var)\s+([A-Za-z_][A-Za-z0-9_]*)"),
)

class SemanticVerifierError(ProviderError):
    pass

class ChangedFileList(list[str]):
    def __init__(self, values: list[str], repo: Path) -> None:
        super().__init__(values)
        self.repo = repo

@dataclass(frozen=True)
class SemanticSettings:
    enabled: bool
    max_schema_retries: int = DEFAULT_MAX_SCHEMA_RETRIES
    max_repair_attempts: int = DEFAULT_MAX_REPAIR_ATTEMPTS
