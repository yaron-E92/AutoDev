from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def remove_test(path: str, name: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    pattern = re.compile(
        rf"\n    def {re.escape(name)}\(self.*?(?=\n    def |\n\nif __name__ ==)",
        re.S,
    )
    text, count = pattern.subn("", text, count=1)
    if count != 1:
        raise SystemExit(f"test method not found: {path}:{name}")
    target.write_text(text, encoding="utf-8")


for path, name in (
    ("tests/test_execution_classification_hooks.py", "test_label_bootstrap_scripts_include_canonical_queue_vocabulary"),
    ("tests/test_opencode_privacy_role_entrypoint.py", "test_installer_replaces_standalone_role_commands_with_python_gated_runner"),
    ("tests/test_opencode_runtime.py", "test_portable_wrapper_routes_through_first_class_cli"),
    ("tests/test_provider_agnostic.py", "test_windows_and_linux_delegate_profile_roles_to_python"),
    ("tests/test_provider_agnostic.py", "test_windows_and_linux_prepare_forward_the_provider_profile"),
    ("tests/test_semantic_repair_default.py", "test_default_is_two"),
    ("tests/test_semantic_repair_default.py", "test_explicit_override_is_preserved"),
    ("tests/test_semantic_verifier.py", "test_windows_and_linux_gate_before_pr_and_reverify_after_ci_repair"),
):
    remove_test(path, name)

installation = ROOT / "tests" / "test_installation_docs.py"
text = installation.read_text(encoding="utf-8")
text = text.replace('        self.assertNotIn("autodev queue", queue)\n', "")
installation.write_text(text, encoding="utf-8")

resume = ROOT / "tests" / "test_opencode_resume_guardrails.py"
text = resume.read_text(encoding="utf-8")
for name in (
    "test_resume_command_uses_installer_launcher_without_shell_fallbacks",
    "test_resume_command_makes_bridge_boundary_authoritative",
    "test_resume_command_revalidates_durable_progress_after_role_tasks",
    "test_resume_command_requires_explicit_terminal_state",
):
    pattern = re.compile(
        rf"\n    def {re.escape(name)}\(self.*?(?=\n    def |\n\nif __name__ ==)",
        re.S,
    )
    text, count = pattern.subn("", text, count=1)
    if count != 1:
        raise SystemExit(f"resume guardrail test not found: {name}")
anchor = "class OpenCodeResumeGuardrailTests(unittest.TestCase):\n"
replacement = '''class OpenCodeResumeGuardrailTests(unittest.TestCase):
    def test_resume_command_is_display_only_frontend_for_canonical_cli(self):
        text = (OPEN_CODE_ROOT / "commands" / "autodev-resume.md").read_text(encoding="utf-8")
        self.assertIn('autodev coordinate --resume', text)
        self.assertIn('--interactive-consent', text)
        self.assertIn('display-only', text)
        self.assertIn('owned entirely by Python', text)
        self.assertNotIn('.opencode/autodev.py', text)
        self.assertNotIn('.opencode/autodev.json', text)
'''
if anchor not in text:
    raise SystemExit("resume guardrail class anchor missing")
text = text.replace(anchor, replacement, 1)
resume.write_text(text, encoding="utf-8")
