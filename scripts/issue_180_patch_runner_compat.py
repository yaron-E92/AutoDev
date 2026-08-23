from __future__ import annotations

from pathlib import Path


CONTRACT = Path("automation/issue_runner_contract.py")
CORE = Path("automation/run_real_issue_core.py")
PUBLIC = Path("automation/run_real_issue.py")


def require(text: str, needle: str, description: str) -> None:
    if needle not in text:
        raise SystemExit(f"generated runner invariant missing: {description}")


def main() -> None:
    contract = CONTRACT.read_text(encoding="utf-8")
    core = CORE.read_text(encoding="utf-8")
    public = PUBLIC.read_text(encoding="utf-8")

    if contract.count("@dataclass(frozen=True)") < 3:
        raise SystemExit("generated runner contract lost dataclass decorators")

    for name, text in (("core", core), ("public", public)):
        require(text, "_COMPAT_ORIGINALS", f"{name} compatibility originals")
        require(text, "_COMPAT_BASELINE", f"{name} compatibility baseline")
        require(text, "current is baseline", f"{name} monkeypatch discrimination")

    require(public, "collect_changed_files", "public imported compatibility surface")


if __name__ == "__main__":
    main()
