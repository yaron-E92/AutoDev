from __future__ import annotations

from pathlib import Path


REPO_TOOL_ROOT = Path(__file__).resolve().parents[1]

OLLAMA_CHAT_URL = "http://localhost:11434/api/chat"

DEFAULT_MAX_CHARS_PER_AREA = 50000

DEFAULT_READER_NUM_PREDICT = 1800

DEFAULT_SYNTH_NUM_PREDICT = 2200

DEFAULT_CODER_NUM_PREDICT = 2200

MAX_FILE_BYTES = 250000

PREFERRED_SOLUTION_FILTER_MARKERS = (
    "no-gui",
    "nogui",
    "headless",
    "backend",
    "server",
    "api",
    "ci",
    "test",
)

MARKDOWN_SMOKE_SCRIPT = """mapfile -t markdown_files < <(git ls-files '*.md')
if ((${#markdown_files[@]} == 0)); then
  echo "No markdown files tracked; skipping markdown smoke check."
  exit 0
fi

if grep -nE $'\t|[ \t]+$' "${markdown_files[@]}"; then
  echo "Markdown smoke check failed: tabs or trailing whitespace found." >&2
  exit 1
fi"""

SUPPORTED_AREAS = ("backend", "web", "maui", "ci", "tests", "docs", "api-client")

DEFAULT_AUTO_AREAS = ("backend", "web", "maui", "ci")

INCLUDED_SUFFIXES = {
    ".cs",
    ".csproj",
    ".sln",
    ".slnf",
    ".xaml",
    ".xml",
    ".json",
    ".md",
    ".yml",
    ".yaml",
    ".props",
    ".targets",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".css",
    ".html",
    ".sh",
    ".ps1",
    ".py",
    ".toml",
    ".lock",
}

INCLUDED_FILENAMES = {
    "AGENTS.md",
    "README.md",
    "CONTRIBUTING.md",
    "Dockerfile",
    "Makefile",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "bun.lockb",
    "Pipfile.lock",
    "poetry.lock",
    "Directory.Build.props",
    "Directory.Packages.props",
    "MauiProgram.cs",
    "App.xaml",
    "Program.cs",
}

EXCLUDED_DIRS = {
    ".git",
    ".vs",
    ".vscode",
    "bin",
    "obj",
    "node_modules",
    ".autodev-run",
    ".idea",
    ".cache",
    ".benchmark-results",
    "__pycache__",
    "TestResults",
    "dist",
    "build",
    "coverage",
}

PRIORITY_PATTERNS = (
    "AGENTS.md",
    "README.md",
    ".github/workflows/*.yml",
    ".github/workflows/*.yaml",
    "*.sln",
    "*.slnf",
    "*.csproj",
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "bun.lockb",
    "tsconfig*",
    "vite.config*",
    "MauiProgram.cs",
    "App.xaml",
    "Program.cs",
    "Directory.Build.props",
    "Directory.Packages.props",
    "docs/*",
    "doc/*",
    "adr/*",
    "ADRs/*",
)

AREA_HINTS = {
    "backend": {
        "keywords": ("backend", "server", "api", "database", "db", "ef", "migration"),
        "path_patterns": (
            "*backend*",
            "*server*",
            "*api*",
            "*.sln",
            "*.slnf",
            "*.csproj",
            "Program.cs",
            "Directory.Build.props",
            "Directory.Packages.props",
        ),
    },
    "web": {
        "keywords": ("web", "frontend", "react", "vite", "typescript", "browser", "ui"),
        "path_patterns": (
            "*web*",
            "*frontend*",
            "*react*",
            "package.json",
            "package-lock.json",
            "pnpm-lock.yaml",
            "yarn.lock",
            "tsconfig*",
            "vite.config*",
            "*.ts",
            "*.tsx",
            "*.js",
            "*.jsx",
            "*.css",
            "*.html",
        ),
    },
    "maui": {
        "keywords": ("maui", "mobile", "desktop", "android", "ios", "xaml"),
        "path_patterns": (
            "*maui*",
            "*mobile*",
            "*android*",
            "*ios*",
            "*.xaml",
            "MauiProgram.cs",
            "App.xaml",
        ),
    },
    "ci": {
        "keywords": ("ci", "workflow", "github actions", "build", "verify", "pipeline"),
        "path_patterns": (
            ".github/workflows/*.yml",
            ".github/workflows/*.yaml",
            "*workflow*",
            "*ci*",
            "*.sh",
            "*.ps1",
            "codex-profiles.json",
        ),
    },
    "tests": {
        "keywords": ("test", "tests", "verification", "xunit", "pytest", "playwright"),
        "path_patterns": (
            "*test*",
            "*tests*",
            "*.Tests/*",
            "*.Test/*",
            "pytest.ini",
            "playwright.config*",
        ),
    },
    "docs": {
        "keywords": ("docs", "documentation", "readme", "adr", "guide"),
        "path_patterns": (
            "README.md",
            "CONTRIBUTING.md",
            "docs/*",
            "doc/*",
            "adr/*",
            "ADRs/*",
            "*.md",
        ),
    },
    "api-client": {
        "keywords": ("api client", "client", "sdk", "http client", "openapi"),
        "path_patterns": (
            "*api-client*",
            "*apiclient*",
            "*client*",
            "*sdk*",
            "*openapi*",
            "*swagger*",
            "*generated*",
        ),
    },
}
