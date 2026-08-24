from __future__ import annotations

import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

FILES = (
    "automation/create_issues_from_description.py",
    "automation/ollama_cloud_preflight.py",
    "docs/create-issues-from-description.md",
    "tests/test_create_issues_from_description.py",
    "tests/test_ollama_cloud_preflight.py",
    "linux/scripts/create-issues-from-description.sh",
    "windows/scripts/create-issues-from-description.ps1",
    "scripts/install-opencode.ps1",
    "linux/scripts/setup-check.sh",
    "linux/scripts/smoke-test.sh",
    "linux/systemd/codex-automation@.service",
    "linux/systemd/codex-automation@.timer",
)
DIRECTORIES = (
    "tests/fixtures/eval",
    "ollama-aliases",
)


def remove_path(relative: str) -> None:
    path = ROOT / relative
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def replace(path: str, old: str, new: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"expected text not found in {path}: {old[:80]!r}")
    target.write_text(text.replace(old, new), encoding="utf-8")


def main() -> int:
    for relative in FILES:
        remove_path(relative)
    for relative in DIRECTORIES:
        remove_path(relative)

    readme = ROOT / "README.md"
    text = readme.read_text(encoding="utf-8")
    text = text.replace(
        "AutoDev also includes helpers for turning rough task descriptions into structured GitHub issues and running a real local issue flow. See `docs/create-issues-from-description.md` and `docs/run-real-issue.md`.\n\n",
        "",
    )
    start = text.find("## Local LLM model aliases\n")
    end = text.find("---\n\n## 6. GitHub labels", start)
    if start < 0 or end < 0:
        raise SystemExit("README legacy local-model/benchmark section boundaries not found")
    text = text[:start] + text[end:]
    readme.write_text(text, encoding="utf-8")

    opencode = ROOT / "docs/opencode.md"
    text = opencode.read_text(encoding="utf-8")
    pattern = re.compile(
        r"\nThe existing convenience wrapper remains available:\n\n```powershell\n"
        r"pwsh -File \.\\scripts\\install-opencode\.ps1 `\n"
        r"  -TargetRepository C:\\source\\repos\\TARGET_REPOSITORY\n```\n",
    )
    text, count = pattern.subn("", text)
    if count != 1:
        raise SystemExit(f"expected one obsolete install-opencode wrapper section, found {count}")
    opencode.write_text(text, encoding="utf-8")

    model_roles = ROOT / "docs/model-roles.md"
    text = model_roles.read_text(encoding="utf-8")
    start = text.find("### Run the preflight\n", text.find("## Opt-in Ollama Cloud profile"))
    end = text.find("### Use the profile\n", start)
    if start < 0 or end < 0:
        raise SystemExit("Ollama Cloud preflight subsection boundaries not found")
    replacement = (
        "### Validate access\n\n"
        "Use Ollama's normal CLI to confirm the configured cloud models are available before running AutoDev. "
        "AutoDev no longer ships a separate Ollama-specific preflight command; provider/runtime failures are handled by the canonical execution path.\n\n"
    )
    text = text[:start] + replacement + text[end:]
    model_roles.write_text(text, encoding="utf-8")

    architecture = ROOT / "tests/test_python_architecture.py"
    text = architecture.read_text(encoding="utf-8")
    marker = '    "automation/eval_harness_core.py",\n'
    addition = (
        marker
        + '    "automation/create_issues_from_description.py",\n'
        + '    "automation/ollama_cloud_preflight.py",\n'
    )
    if marker not in text:
        raise SystemExit("architecture removed-path insertion marker not found")
    architecture.write_text(text.replace(marker, addition, 1), encoding="utf-8")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
