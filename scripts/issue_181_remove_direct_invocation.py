from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def remove_method(path: Path, name: str) -> None:
    text = path.read_text(encoding="utf-8")
    marker = f"    def {name}("
    start = text.find(marker)
    if start < 0:
        raise SystemExit(f"missing method {name} in {path}")
    end = text.find("\n    def ", start + len(marker))
    if end < 0:
        end = text.find("\n\nif __name__", start)
    if end < 0:
        raise SystemExit(f"cannot bound method {name} in {path}")
    path.write_text(text[:start] + text[end:], encoding="utf-8")


def clean_prompt_policies() -> None:
    path = ROOT / "automation" / "prompt_policies.py"
    text = path.read_text(encoding="utf-8")
    text = text.replace("from automation.model_roles import MODEL_ROLES\n", "")
    marker = 'SUPPORTED_POLICY_MODES = {"off", "lite", "full", "review"}\n'
    replacement = marker + 'MODEL_ROLES = ("reader", "synthesizer", "planner", "implementer", "fixer", "verifier")\n'
    if marker not in text:
        raise SystemExit("prompt policy marker missing")
    text = text.replace(marker, replacement, 1)
    path.write_text(text, encoding="utf-8")


def clean_semantic_invocation() -> None:
    path = ROOT / "automation" / "semantic_invocation.py"
    text = path.read_text(encoding="utf-8")
    start = text.find("from typing import Callable\n")
    body = text.find("def prepare_semantic_repair_prompt(\n")
    if start < 0 or body < 0:
        raise SystemExit("semantic invocation markers missing")
    imports = '''from automation.semantic_evidence import (\n    collect_changed_files,\n    collect_current_diff,\n)\nfrom automation.semantic_prompts import build_semantic_repair_prompt\nfrom automation.semantic_storage import (\n    _read_json,\n    _read_text,\n)\n\n'''
    text = text[:start] + imports + text[body:]
    path.write_text(text, encoding="utf-8")


def clean_semantic_contract() -> None:
    path = ROOT / "automation" / "semantic_contract.py"
    text = path.read_text(encoding="utf-8")
    text = text.replace(
        "from automation.provider_contract import ModelConfig, ModelProvider, ProviderError\n",
        "from automation.provider_contract import ProviderError\n",
    )
    text = text.replace("from automation.provider_factory import load_provider_config\n", "")
    path.write_text(text, encoding="utf-8")


def clean_tests() -> None:
    path = ROOT / "tests" / "test_model_roles.py"
    text = path.read_text(encoding="utf-8")
    text = text.replace("from automation.provider_contract import ModelConfig\n", "")
    text = text.replace("from automation.provider_mock import MockProvider\n", "")
    text = text.replace("from automation.model_roles import ModelInvocationError, invoke_model\n", "")
    text = text.replace("class ModelRoleTests(unittest.TestCase):", "class PromptPolicyTests(unittest.TestCase):")
    path.write_text(text, encoding="utf-8")
    remove_method(path, "test_failed_call_has_safe_role_metadata")

    path = ROOT / "tests" / "test_semantic_verifier.py"
    text = path.read_text(encoding="utf-8")
    text = text.replace("from automation.provider_contract import ModelConfig, ProviderError\n", "")
    text = text.replace("from automation.provider_mock import MockProvider\n", "")
    text = text.replace("from automation.prompt_policies import resolve_prompt_policies\n", "")
    text = text.replace("from automation.semantic_invocation import invoke_semantic_verifier\n", "")
    path.write_text(text, encoding="utf-8")
    remove_method(path, "test_schema_retry_uses_verifier_again_and_records_separate_telemetry")

    path = ROOT / "tests" / "test_headroom.py"
    text = path.read_text(encoding="utf-8")
    text = text.replace("from automation.model_roles import invoke_model\n", "")
    path.write_text(text, encoding="utf-8")

    path = ROOT / "tests" / "test_privacy.py"
    text = path.read_text(encoding="utf-8")
    text = text.replace("from automation.model_roles import ModelInvocationError, invoke_model\n", "")
    old = '''            with self.assertRaises(ModelInvocationError) as raised:\n                invoke_model(provider, config, "SECRET REPOSITORY PROMPT", role="planner", repo=repo)\n\n        self.assertFalse(provider.called)\n        self.assertEqual(raised.exception.classification, "privacy_blocked")\n        self.assertNotIn("SECRET REPOSITORY PROMPT", json.dumps(raised.exception.record))\n'''
    new = '''            with self.assertRaises(privacy.PrivacyError):\n                privacy.authorize_direct_call(provider, config, role="planner", repo=repo)\n\n        self.assertFalse(provider.called)\n'''
    if old not in text:
        raise SystemExit("blocked privacy invocation fixture missing")
    text = text.replace(old, new, 1)
    text = text.replace(
        '            invoke_model(provider, config, "TOP SECRET PROMPT", role="planner", repo=repo)\n',
        '            privacy.authorize_direct_call(provider, config, role="planner", repo=repo)\n            provider.invoke("TOP SECRET PROMPT", model=config.model, timeout_seconds=config.timeout_seconds)\n',
        1,
    )
    path.write_text(text, encoding="utf-8")


def main() -> None:
    clean_prompt_policies()
    clean_semantic_invocation()
    clean_semantic_contract()
    clean_tests()
    (ROOT / "automation" / "model_roles.py").unlink()


if __name__ == "__main__":
    main()
