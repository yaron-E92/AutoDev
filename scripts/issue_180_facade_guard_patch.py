from pathlib import Path

root = Path(__file__).resolve().parents[1]

# Tighten the facade migration's own residual-reference guard so responsibility
# modules such as opencode_resume_execution are not mistaken for the facade.
path = root / "scripts" / "issue_180_remove_module_facades.py"
text = path.read_text(encoding="utf-8")
if "import re\n" not in text:
    text = text.replace("import ast\n", "import ast\nimport re\n", 1)
old = '''    for facade in FACADES:
        if (
            f"from automation import {facade}" in updated
            or f"automation.{facade}" in updated
            or f"{facade}." in updated
        ):
            raise SystemExit(
                f"facade reference remains in {path.relative_to(ROOT)}: {facade}"
            )
'''
new = '''    for facade in FACADES:
        escaped = re.escape(facade)
        if (
            re.search(rf"\\b{escaped}\\.", updated)
            or re.search(rf"from\\s+automation\\s+import[^\\n]*\\b{escaped}\\b", updated)
            or re.search(rf"from\\s+automation\\.{escaped}\\s+import\\b", updated)
        ):
            raise SystemExit(
                f"facade reference remains in {path.relative_to(ROOT)}: {facade}"
            )
'''
if old in text:
    text = text.replace(old, new)
elif new not in text:
    raise SystemExit("facade guard block not found")
path.write_text(text, encoding="utf-8")

# windows_semantic_order historically used the opencode_resume compatibility
# facade both as a function namespace and as storage for an installation flag.
# Move both responsibilities to their real owners before the generic facade
# retargeter runs.  Keep the idempotence sentinel local to the hook module.
path = root / "automation" / "windows_semantic_order.py"
text = path.read_text(encoding="utf-8")
text = text.replace(
    "from automation import opencode_resume, run_manifest, windows_verification, workflow_stages\n",
    "from automation import (\n"
    "    opencode_resume_checkpoint,\n"
    "    opencode_resume_execution,\n"
    "    opencode_resume_status,\n"
    "    run_manifest,\n"
    "    windows_verification,\n"
    "    workflow_stages,\n"
    ")\n",
)
if "_WINDOWS_SEMANTIC_ORDER_INSTALLED = False\n" not in text:
    anchor = "MAX_WINDOWS_EVIDENCE_CHARS = 12_000\n"
    if anchor not in text:
        raise SystemExit("windows semantic-order constant anchor missing")
    text = text.replace(anchor, anchor + "_WINDOWS_SEMANTIC_ORDER_INSTALLED = False\n", 1)
text = text.replace(
    '    windows_verification.install_opencode_hooks()\n    if getattr(opencode_resume, "_autodev_windows_semantic_order_installed", False):\n        return\n',
    '    global _WINDOWS_SEMANTIC_ORDER_INSTALLED\n\n'
    '    windows_verification.install_opencode_hooks()\n'
    '    if _WINDOWS_SEMANTIC_ORDER_INSTALLED:\n'
    '        return\n',
)
text = text.replace("opencode_resume.resume_action", "opencode_resume_status.resume_action")
text = text.replace("opencode_resume.checkpoint_stage", "opencode_resume_checkpoint.checkpoint_stage")
text = text.replace("opencode_resume.resume", "opencode_resume_execution.resume")
text = text.replace(
    "    opencode_resume._autodev_windows_semantic_order_installed = True\n",
    "    _WINDOWS_SEMANTIC_ORDER_INSTALLED = True\n",
)
if "opencode_resume." in text or "_autodev_windows_semantic_order_installed" in text:
    raise SystemExit("windows semantic-order resume facade seam remains")
path.write_text(text, encoding="utf-8")
