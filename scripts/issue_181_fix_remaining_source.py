from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_all(relative: str, old: str, new: str) -> None:
    path = ROOT / relative
    text = path.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"expected text not found in {relative}: {old!r}")
    path.write_text(text.replace(old, new), encoding="utf-8")


def main() -> int:
    replace_all(
        "automation/ci_outcomes.py",
        "wait for CI to finish, then run `python3 .opencode/autodev.py coordinate --resume`",
        "wait for CI to finish, then run `autodev coordinate --resume`",
    )

    report = ROOT / "automation" / "non_success_report.py"
    text = report.read_text(encoding="utf-8")
    old = "python3 .opencode/autodev.py coordinate --resume"
    if old not in text:
        raise SystemExit("non-success report no longer contains expected bridge resume commands")
    report.write_text(text.replace(old, "autodev coordinate --resume"), encoding="utf-8")

    replace_all(
        "automation/opencode_adapter_roles.py",
        "Run exactly `python .opencode/autodev.py accept --role fixer` when the targeted edit is complete.",
        "Run exactly `autodev accept --role fixer` when the targeted edit is complete.",
    )

    repo_setup = ROOT / "automation" / "repo_setup.py"
    text = repo_setup.read_text(encoding="utf-8")
    old_block = '''    else:\n        stale = []\n        for name in opencode_adapter_contract.AGENT_FILES:\n            text = (repo / ".opencode" / "agents" / name).read_text(encoding="utf-8")\n            if ".opencode/autodev.json" in text:\n                stale.append(name)\n        if stale:\n            checks.append(\n                DoctorCheck(\n                    "opencode-assets",\n                    "error",\n                    "legacy agent launcher configuration remains: " + ", ".join(stale),\n                    True,\n                )\n            )\n        else:\n            checks.append(\n                DoctorCheck(\n                    "opencode-assets",\n                    "ok",\n                    "OpenCode commands/agents use the canonical AutoDev launcher contract",\n                )\n            )\n'''
    new_block = '''    else:\n        checks.append(\n            DoctorCheck(\n                "opencode-assets",\n                "ok",\n                "OpenCode commands/agents use the canonical AutoDev launcher contract",\n            )\n        )\n'''
    if old_block not in text:
        raise SystemExit("repo doctor legacy OpenCode scan block not found")
    repo_setup.write_text(text.replace(old_block, new_block, 1), encoding="utf-8")

    forbidden = (
        ".opencode/autodev.py",
        ".opencode/autodev.ps1",
        ".opencode/autodev.json",
        "python .opencode/autodev.py",
        "python3 .opencode/autodev.py",
    )
    for relative in (
        "automation/ci_outcomes.py",
        "automation/non_success_report.py",
        "automation/opencode_adapter_roles.py",
        "automation/repo_setup.py",
    ):
        current = (ROOT / relative).read_text(encoding="utf-8")
        for needle in forbidden:
            if needle in current:
                raise SystemExit(f"retired bridge reference remains in {relative}: {needle}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
