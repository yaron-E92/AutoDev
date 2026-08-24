from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AUTOMATION = ROOT / "automation"
TESTS = ROOT / "tests"

ROLE_RENAMES = {
    "role_coord_cli.py": "role_coordinator_cli.py",
    "role_coord_contract.py": "role_coordinator_contract.py",
    "role_coord_flow.py": "role_coordinator_flow.py",
    "role_coord_runtime.py": "role_coordinator_runtime.py",
    "role_coord_stages.py": "role_coordinator_stages.py",
    "role_coord_state.py": "role_coordinator_state.py",
}
OPENCODE_OBSOLETE = [
    "opencode_coordinator.py",
    "opencode_coord_cli.py",
    "opencode_coord_contract.py",
    "opencode_coord_flow.py",
    "opencode_coord_runtime.py",
    "opencode_coord_stages.py",
    "opencode_coord_state.py",
]


def replace(path: Path, before: str, after: str, *, required: bool = False) -> None:
    text = path.read_text(encoding="utf-8")
    if required and before not in text:
        raise SystemExit(f"expected pattern not found in {path.relative_to(ROOT)}: {before!r}")
    updated = text.replace(before, after)
    if updated != text:
        path.write_text(updated, encoding="utf-8")


def remove_block(path: Path, start: str, end: str) -> None:
    text = path.read_text(encoding="utf-8")
    start_index = text.find(start)
    if start_index < 0:
        return
    end_index = text.find(end, start_index)
    if end_index < 0:
        raise SystemExit(f"cannot find end marker in {path.relative_to(ROOT)}")
    path.write_text(text[:start_index] + text[end_index:], encoding="utf-8")


def rename_role_modules() -> None:
    for old_name, new_name in ROLE_RENAMES.items():
        old = AUTOMATION / old_name
        new = AUTOMATION / new_name
        if old.exists():
            if new.exists():
                raise SystemExit(f"target already exists: {new}")
            old.rename(new)
    for path in ROOT.rglob("*.py"):
        if ".git" in path.parts:
            continue
        replace(path, "role_coord_", "role_coordinator_")


def patch_role_consumers() -> None:
    path = AUTOMATION / "opencode_github_entrypoint.py"
    text = path.read_text(encoding="utf-8")
    text = text.replace("    role_coordinator as opencode_coordinator,\n", "")
    anchor = "from automation import (\n"
    extra = '''from automation import (\n    role_coordinator_cli,\n    role_coordinator_contract,\n    role_coordinator_flow,\n    role_coordinator_stages,\n)\n\n'''
    close = ")\n\n\nSUCCESSFUL_TERMINAL_STATES"
    if extra not in text:
        text = text.replace(close, ")\n\n" + extra + "SUCCESSFUL_TERMINAL_STATES", 1)
    replacements = {
        "opencode_coordinator.coordinate": "role_coordinator_flow.coordinate",
        "opencode_coordinator.invalidations": "role_coordinator_cli.invalidations",
        "opencode_coordinator.RoleCoordinatorError": "role_coordinator_contract.RoleCoordinatorError",
        "opencode_coordinator.terminal_payload": "role_coordinator_stages.terminal_payload",
    }
    for before, after in replacements.items():
        text = text.replace(before, after)
    if "opencode_coordinator" in text or "role_coordinator as" in text:
        raise SystemExit("opencode_github_entrypoint still uses coordinator facade alias")
    path.write_text(text, encoding="utf-8")

    path = AUTOMATION / "opencode_role_entrypoint.py"
    text = path.read_text(encoding="utf-8")
    text = text.replace("    role_coordinator,\n", "    role_coordinator_contract,\n    role_coordinator_runtime,\n")
    text = text.replace("role_coordinator.run_role", "role_coordinator_runtime.run_role")
    text = text.replace("role_coordinator.RoleCoordinatorError", "role_coordinator_contract.RoleCoordinatorError")
    if "role_coordinator." in text:
        raise SystemExit("opencode_role_entrypoint still uses role coordinator facade")
    path.write_text(text, encoding="utf-8")

    path = AUTOMATION / "execution_classification_evidence.py"
    text = path.read_text(encoding="utf-8")
    text = text.replace("    role_coordinator,\n", "    opencode_adapter,\n    role_coordinator_flow,\n    role_resume,\n    role_runtime,\n")
    replacements = {
        "role_coordinator.run_stage": "role_coordinator_flow.run_stage",
        "role_coordinator.role_resume": "role_resume",
        "role_coordinator.opencode_adapter": "opencode_adapter",
        "role_coordinator.role_runtime": "role_runtime",
    }
    for before, after in replacements.items():
        text = text.replace(before, after)
    if "role_coordinator." in text:
        raise SystemExit("execution classification evidence still uses coordinator facade")
    path.write_text(text, encoding="utf-8")

    path = AUTOMATION / "execution_classification_hooks.py"
    text = path.read_text(encoding="utf-8")
    text = text.replace("    role_coordinator,\n", "    role_coordinator_flow,\n")
    text = text.replace("role_coordinator.", "role_coordinator_flow.")
    path.write_text(text, encoding="utf-8")

    path = AUTOMATION / "role_workflow_hooks.py"
    text = path.read_text(encoding="utf-8")
    text = text.replace("    role_coordinator,\n", "    role_coordinator_contract,\n    role_coordinator_flow,\n")
    text = text.replace("role_coordinator.REPAIR_KINDS", "role_coordinator_contract.REPAIR_KINDS")
    text = text.replace("role_coordinator.run_stage", "role_coordinator_flow.run_stage")
    text = text.replace("role_coordinator.coordinate", "role_coordinator_flow.coordinate")
    if "role_coordinator." in text:
        raise SystemExit("role_workflow_hooks still uses coordinator facade")
    path.write_text(text, encoding="utf-8")


def remove_specialized_opencode_hooks() -> None:
    path = AUTOMATION / "ci_outcomes.py"
    remove_block(path, "    from automation import opencode_coordinator\n", "\n\ndef install() -> None:")

    path = AUTOMATION / "privacy_consent.py"
    replace(path, "from automation import opencode_adapter, opencode_coordinator, opencode_resume, privacy, run_manifest, workflow_stages\n", "from automation import opencode_adapter, opencode_resume, privacy, run_manifest, workflow_stages\n")
    remove_block(path, "def _install_preflight_hook() -> None:\n", "\n\ndef install() -> None:")
    replace(path, "    _install_preflight_hook()\n", "")

    path = AUTOMATION / "privacy_grant_cli.py"
    replace(path, "from automation import opencode_adapter, opencode_coordinator, privacy_consent\n", "from automation import opencode_adapter, opencode_cli, privacy_consent\n")
    replace(path, "opencode_coordinator.opencode_cli.resolve_opencode_cli", "opencode_cli.resolve_opencode_cli")

    path = AUTOMATION / "windows_verification_hooks.py"
    replace(path, "from automation import opencode_adapter, opencode_coordinator, opencode_resume, run_manifest, workflow_stages\n", "from automation import opencode_adapter, opencode_resume, run_manifest, workflow_stages\n")
    replace(path, "    opencode_coordinator.REPAIR_KINDS[\"fixer-windows\"] = \"windows\"\n", "")

    path = AUTOMATION / "opencode_failure_entrypoint.py"
    text = path.read_text(encoding="utf-8")
    text = text.replace("import argparse\n", "").replace("import sys\n", "")
    text = text.replace("    opencode_adapter,\n    opencode_coordinator,\n    opencode_resume,\n", "")
    marker = "\ndef run(argv: list[str] | None = None) -> int:\n"
    if marker in text:
        text = text.split(marker, 1)[0].rstrip() + "\n"
    if "opencode_coordinator" in text or "def main(" in text:
        raise SystemExit("opencode_failure_entrypoint still contains obsolete coordinator CLI")
    path.write_text(text, encoding="utf-8")


def patch_tests() -> None:
    obsolete_test = TESTS / "test_opencode_coordinator.py"
    obsolete_test.unlink(missing_ok=True)

    path = TESTS / "test_role_runtime.py"
    text = path.read_text(encoding="utf-8")
    text = text.replace("    role_coordinator,\n", "    role_coordinator_flow,\n    role_coordinator_runtime,\n")
    replacements = {
        "role_coordinator.opencode_runtime": "role_coordinator_flow.opencode_runtime",
        "role_coordinator.run_stage": "role_coordinator_flow.run_stage",
        "role_coordinator._resume_payload": "role_coordinator_flow._resume_payload",
        "role_coordinator.coordinate": "role_coordinator_flow.coordinate",
        "role_coordinator._prepare_role": "role_coordinator_runtime._prepare_role",
        "role_coordinator._accept_role": "role_coordinator_runtime._accept_role",
        "role_coordinator.role_acceptance": "role_coordinator_runtime.role_acceptance",
        "role_coordinator.RoleCoordinatorError": "role_coordinator_runtime.RoleCoordinatorError",
        "role_coordinator.run_role": "role_coordinator_runtime.run_role",
    }
    for before, after in replacements.items():
        text = text.replace(before, after)
    if "role_coordinator." in text:
        raise SystemExit("test_role_runtime still uses coordinator facade")
    path.write_text(text, encoding="utf-8")

    path = TESTS / "test_ci_outcomes.py"
    text = path.read_text(encoding="utf-8")
    text = text.replace("from automation import ci_outcomes, opencode_coordinator, workflow_stages\n", "from automation import ci_outcomes, workflow_stages\n")
    text = text.replace('            "coordinator_run_stage": opencode_coordinator.run_stage,\n            "coordinator_coordinate": opencode_coordinator.coordinate,\n', "")
    text = text.replace('        opencode_coordinator.run_stage = originals["coordinator_run_stage"]  # type: ignore[assignment]\n        opencode_coordinator.coordinate = originals["coordinator_coordinate"]  # type: ignore[assignment]\n', "")
    start = text.find("    def test_waiting_stage_short_circuits_the_python_coordinator(self):\n")
    if start >= 0:
        end = text.find("    def test_ready_proof_accepts_same_non_failing_semantics", start)
        if end < 0:
            raise SystemExit("cannot remove obsolete specialized coordinator CI test")
        text = text[:start] + text[end:]
    path.write_text(text, encoding="utf-8")

    # Remaining generic coordinator tests patch the public flow module rather than a facade.
    for name in ("test_role_workflow_hooks.py", "test_execution_classification_hooks.py"):
        path = TESTS / name
        text = path.read_text(encoding="utf-8")
        text = text.replace("role_coordinator,", "role_coordinator_flow,")
        text = text.replace("role_coordinator.", "role_coordinator_flow.")
        path.write_text(text, encoding="utf-8")

    path = TESTS / "test_opencode_privacy_role_entrypoint.py"
    text = path.read_text(encoding="utf-8")
    text = text.replace("role_coordinator,", "role_coordinator_runtime,")
    text = text.replace("role_coordinator.", "role_coordinator_runtime.")
    path.write_text(text, encoding="utf-8")


def delete_facades_and_specialized_stack() -> None:
    (AUTOMATION / "role_coordinator.py").unlink(missing_ok=True)
    for name in OPENCODE_OBSOLETE:
        (AUTOMATION / name).unlink(missing_ok=True)


def guard() -> None:
    offenders: list[str] = []
    for path in [*AUTOMATION.rglob("*.py"), *TESTS.rglob("*.py")]:
        text = path.read_text(encoding="utf-8")
        if "role_coord_" in text or "opencode_coord_" in text or "opencode_coordinator" in text:
            offenders.append(str(path.relative_to(ROOT)))
    if offenders:
        raise SystemExit("obsolete coordinator references remain: " + ", ".join(sorted(set(offenders))))
    if (AUTOMATION / "role_coordinator.py").exists():
        raise SystemExit("role_coordinator facade still exists")
    for name in OPENCODE_OBSOLETE:
        if (AUTOMATION / name).exists():
            raise SystemExit(f"obsolete OpenCode coordinator file remains: {name}")


def main() -> None:
    rename_role_modules()
    patch_role_consumers()
    remove_specialized_opencode_hooks()
    patch_tests()
    delete_facades_and_specialized_stack()
    guard()


if __name__ == "__main__":
    main()
