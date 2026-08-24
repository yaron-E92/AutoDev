from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def patch_command_group_tests() -> None:
    path = ROOT / "tests/test_command_group_recommendations.py"
    text = path.read_text(encoding="utf-8")
    text = text.replace("import importlib.util\n", "")
    import_anchor = '''from area_reader.recommendations import (\n    ALL_COMMAND_GROUPS,\n    recommend_command_groups,\n)\n'''
    extra = '''from area_reader import cli as area_reader_cli\nfrom area_reader import prompts as area_reader_prompts\nfrom area_reader import provider as area_reader_provider\nfrom area_reader import repository as area_reader_repository\nfrom area_reader import verification as area_reader_verification\n'''
    if extra not in text:
        text = text.replace(import_anchor, import_anchor + extra)
    loader = '''\n\ndef load_area_reader_bench():\n    path = Path(__file__).resolve().parents[1] / "benchmarks" / "local-llm" / "area_reader_bench.py"\n    spec = importlib.util.spec_from_file_location("area_reader_bench", path)\n    module = importlib.util.module_from_spec(spec)\n    spec.loader.exec_module(module)\n    return module\n'''
    text = text.replace(loader, "")
    text = text.replace("        bench = load_area_reader_bench()\n", "")
    replacements = {
        "bench.recommended_command_groups": "area_reader_verification.recommended_command_groups",
        "bench.build_coder_prompt": "area_reader_prompts.build_coder_prompt",
        "bench.build_verification_command_groups": "area_reader_verification.build_verification_command_groups",
        "bench.detect_repo_facts": "area_reader_repository.detect_repo_facts",
        "bench.render_verification_script": "area_reader_verification.render_verification_script",
        "bench.area_for_file": "area_reader_repository.area_for_file",
        "bench.parse_args": "area_reader_cli.parse_args",
        "bench.model_config_from_args": "area_reader_provider.model_config_from_args",
        "test_benchmark_recommendation_wrapper_returns_metadata_shape": "test_recommendation_metadata_shape",
        "test_benchmark_model_only_command_provider_uses_ollama_run": "test_model_only_command_provider_uses_ollama_run",
    }
    for before, after in replacements.items():
        text = text.replace(before, after)
    if "load_area_reader_bench" in text or "bench." in text or "area_reader_bench" in text:
        raise SystemExit("command-group tests still depend on benchmark compatibility wrapper")
    path.write_text(text, encoding="utf-8")


def patch_headroom_tests() -> None:
    path = ROOT / "tests/test_headroom_area_reader.py"
    text = path.read_text(encoding="utf-8")
    text = text.replace("from area_reader.workflow import (", "from area_reader.prompts import (")
    if "area_reader.workflow" in text:
        raise SystemExit("headroom area-reader tests still import workflow facade for prompt helpers")
    path.write_text(text, encoding="utf-8")


def main() -> None:
    patch_command_group_tests()
    patch_headroom_tests()


if __name__ == "__main__":
    main()
