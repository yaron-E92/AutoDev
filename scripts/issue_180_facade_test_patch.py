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
