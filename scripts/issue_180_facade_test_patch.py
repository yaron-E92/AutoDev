from pathlib import Path

root = Path(__file__).resolve().parents[1]

path = root / "tests" / "test_windows_semantic_order.py"
text = path.read_text(encoding="utf-8")
if "    windows_verification_execution,\n" not in text:
    text = text.replace(
        "    windows_semantic_order,\n",
        "    windows_semantic_order,\n    windows_verification_execution,\n",
        1,
    )
text = text.replace(
    'mock.patch.object(windows_verification, "run_after_push"',
    'mock.patch.object(windows_verification_execution, "run_after_push"',
)
path.write_text(text, encoding="utf-8")

path = root / "tests" / "test_role_workflow_hooks.py"
text = path.read_text(encoding="utf-8")
if "    opencode_resume_status,\n" not in text:
    text = text.replace(
        "    opencode_resume,\n",
        "    opencode_resume,\n    opencode_resume_status,\n",
        1,
    )
if "    windows_verification_manifest,\n" not in text:
    text = text.replace(
        "    windows_verification,\n",
        "    windows_verification,\n    windows_verification_manifest,\n",
        1,
    )
text = text.replace(
    '                opencode_resume,\n                "repair_attempts",',
    '                opencode_resume_status,\n                "repair_attempts",',
)
for name in ("payload_metadata", "windows_required", "proof_current"):
    text = text.replace(
        f'                windows_verification,\n                "{name}",',
        f'                windows_verification_manifest,\n                "{name}",',
    )
path.write_text(text, encoding="utf-8")

# semantic_verifier used to provide the executable `python -m` boundary even
# though semantic_cli owns the parser and command implementation. Move that
# boundary to the owner before deleting the facade, then update both platform
# wrappers and their regression assertion.
path = root / "automation" / "semantic_cli.py"
text = path.read_text(encoding="utf-8")
if 'if __name__ == "__main__":' not in text:
    text = text.rstrip() + '\n\n\nif __name__ == "__main__":\n    raise SystemExit(run())\n'
path.write_text(text, encoding="utf-8")

for relative in (
    "linux/scripts/issue-to-pr-cycle.sh",
    "windows/scripts/issue-to-pr-cycle.ps1",
    "tests/test_semantic_verifier.py",
):
    path = root / relative
    text = path.read_text(encoding="utf-8")
    text = text.replace("automation.semantic_verifier", "automation.semantic_cli")
    path.write_text(text, encoding="utf-8")
