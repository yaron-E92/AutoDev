from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def remove_between(path: Path, start_marker: str, end_marker: str) -> None:
    text = path.read_text(encoding="utf-8")
    start = text.find(start_marker)
    end = text.find(end_marker, start)
    if start < 0 or end < 0:
        raise SystemExit(f"cannot bound cleanup in {path}: {start_marker!r}")
    path.write_text(text[:start] + text[end:], encoding="utf-8")


def remove_test_method(path: Path, name: str) -> None:
    text = path.read_text(encoding="utf-8")
    marker = f"    def {name}("
    start = text.find(marker)
    if start < 0:
        raise SystemExit(f"missing test {name} in {path}")
    end = text.find("\n    def ", start + len(marker))
    if end < 0:
        end = text.find("\n\nif __name__", start)
    if end < 0:
        raise SystemExit(f"cannot bound test {name} in {path}")
    path.write_text(text[:start] + text[end:], encoding="utf-8")


def clean_model_roles() -> None:
    path = ROOT / "automation/model_roles.py"
    text = path.read_text(encoding="utf-8")
    text = text.replace("from automation import headroom\n\n", "")
    text = text.replace("from automation.headroom import HeadroomError, resolve_headroom_values\n", "")
    text = text.replace(
        "from automation.provider_factory import model_config_from_values, normalize_provider_name, ollama_command_for_model\n",
        "",
    )
    text = text.replace(
        'ROLE_FALLBACKS = {\n    "reader": "reader",\n    "synthesizer": "reader",\n    "planner": "coder",\n    "implementer": "coder",\n    "fixer": "coder",\n    "verifier": "coder",\n}\n\n',
        "",
    )
    path.write_text(text, encoding="utf-8")
    remove_between(path, "def resolve_role_configs(\n", "def safe_role_metadata(")


def clean_semantic_invocation() -> None:
    path = ROOT / "automation/semantic_invocation.py"
    text = path.read_text(encoding="utf-8")
    text = text.replace("from automation.provider_factory import load_provider_config\n", "")
    text = text.replace("    resolve_role_configs,\n", "")
    marker = "\ndef resolve_profile_roles(\n"
    start = text.find(marker)
    if start < 0:
        raise SystemExit("resolve_profile_roles missing")
    text = text[:start].rstrip() + "\n"
    path.write_text(text, encoding="utf-8")


def clean_tests() -> None:
    path = ROOT / "tests/test_model_roles.py"
    text = path.read_text(encoding="utf-8")
    text = text.replace("import tempfile\n", "")
    text = text.replace("from automation.provider_contract import ModelConfig, ProviderError\n", "from automation.provider_contract import ModelConfig\n")
    text = text.replace(
        "from automation.model_roles import ModelInvocationError, invoke_model, resolve_role_configs\n",
        "from automation.model_roles import ModelInvocationError, invoke_model\n",
    )
    path.write_text(text, encoding="utf-8")
    for name in (
        "setUp",
        "test_version_two_roles_are_independent",
        "test_legacy_reader_coder_fallbacks_and_disabled_verifier",
        "test_explicit_role_wins_over_legacy_cli_override",
        "test_unknown_config_version_is_rejected",
    ):
        remove_test_method(path, name)

    path = ROOT / "tests/test_headroom.py"
    text = path.read_text(encoding="utf-8")
    text = text.replace(
        "from automation.model_roles import invoke_model, resolve_role_configs\n",
        "from automation.model_roles import invoke_model\n",
    )
    path.write_text(text, encoding="utf-8")
    remove_test_method(path, "test_role_resolution_composes_headroom_with_version_two_provider_profile")


def main() -> None:
    clean_model_roles()
    clean_semantic_invocation()
    clean_tests()


if __name__ == "__main__":
    main()
