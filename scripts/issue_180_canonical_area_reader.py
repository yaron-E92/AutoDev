from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

RENAMES = {
    "area_reader_cli.py": "cli.py",
    "area_reader_context.py": "context.py",
    "area_reader_execution.py": "execution.py",
    "area_reader_prompts.py": "prompts.py",
    "area_reader_provider.py": "provider.py",
    "area_reader_repository.py": "repository.py",
    "area_reader_routing.py": "routing.py",
    "area_reader_settings.py": "settings.py",
    "area_reader_storage.py": "storage.py",
    "area_reader_verification.py": "verification.py",
    "command_group_recommendations.py": "recommendations.py",
    "area_reader_workflow.py": "pipeline.py",
    "runner.py": "workflow.py",
}

TEXT_EXTENSIONS = {".py", ".md", ".txt", ".json", ".jsonc", ".yml", ".yaml", ".sh", ".ps1", ".toml"}


def move(source: Path, target: Path) -> None:
    if not source.exists():
        return
    if target.exists():
        raise SystemExit(f"target already exists: {target}")
    source.rename(target)


def replace_all(text: str) -> str:
    replacements = [
        ("area_reader_v2", "area_reader"),
        ("area_reader.area_reader_cli", "area_reader.cli"),
        ("area_reader.area_reader_context", "area_reader.context"),
        ("area_reader.area_reader_execution", "area_reader.execution"),
        ("area_reader.area_reader_prompts", "area_reader.prompts"),
        ("area_reader.area_reader_provider", "area_reader.provider"),
        ("area_reader.area_reader_repository", "area_reader.repository"),
        ("area_reader.area_reader_routing", "area_reader.routing"),
        ("area_reader.area_reader_settings", "area_reader.settings"),
        ("area_reader.area_reader_storage", "area_reader.storage"),
        ("area_reader.area_reader_verification", "area_reader.verification"),
        ("area_reader.command_group_recommendations", "area_reader.recommendations"),
        ("area_reader.area_reader_workflow", "area_reader.pipeline"),
        ("area_reader.runner_core", "area_reader.workflow"),
        ("area_reader.runner", "area_reader.workflow"),
        ("from area_reader import runner as", "from area_reader import workflow as"),
        ("from area_reader import runner\n", "from area_reader import workflow\n"),
        ("area-reader v2", "area-reader"),
        ("Area Reader v2", "Area Reader"),
        ("area reader v2", "area reader"),
    ]
    for before, after in replacements:
        text = text.replace(before, after)
    return text


def patch_workflow(package: Path) -> None:
    path = package / "workflow.py"
    text = path.read_text(encoding="utf-8")
    text = text.replace("from area_reader import workflow as _core\n", "from area_reader import pipeline as _pipeline\n")
    text = text.replace("from area_reader.workflow import *  # noqa: F401,F403\n", "from area_reader.cli import parse_args as _base_parse_args\n")
    text = text.replace("_ORIGINAL_PARSE_ARGS = _core.parse_args", "_ORIGINAL_PARSE_ARGS = _base_parse_args")
    old = '''    original_parse = _core.parse_args\n    original_call = _core.call_provider\n    try:\n        _core.parse_args = parse_args\n        _core.call_provider = call_provider\n        code = _core.main(argv)\n    finally:\n        _core.parse_args = original_parse\n        _core.call_provider = original_call\n'''
    new = '''    code = _pipeline.main(\n        argv,\n        parse_args_fn=parse_args,\n        call_provider_fn=call_provider,\n    )\n'''
    if old not in text and "_pipeline.main(" not in text:
        raise SystemExit("canonical area-reader workflow delegation pattern not found")
    text = text.replace(old, new)
    if "_core" in text or "import *" in text:
        raise SystemExit("area-reader workflow still contains facade compatibility")
    path.write_text(text, encoding="utf-8")


def patch_pipeline(package: Path) -> None:
    path = package / "pipeline.py"
    text = path.read_text(encoding="utf-8")
    text = text.replace("def main(argv=None):\n    args = parse_args(argv)\n", "def main(argv=None, *, parse_args_fn=parse_args, call_provider_fn=call_provider):\n    args = parse_args_fn(argv)\n")
    text = text.replace("run_area_reader(args, repo, out, area, repo_map, files)", "run_area_reader(args, repo, out, area, repo_map, files, call_provider_fn=call_provider_fn)")
    text = text.replace("        synthesis_raw, synthesis_wall_seconds = call_provider(\n", "        synthesis_raw, synthesis_wall_seconds = call_provider_fn(\n")
    text = text.replace("        coder_raw, coder_wall_seconds = call_provider(\n", "        coder_raw, coder_wall_seconds = call_provider_fn(\n")
    if "parse_args_fn" not in text or "call_provider_fn" not in text:
        raise SystemExit("area-reader pipeline dependency injection was not installed")
    path.write_text(text, encoding="utf-8")


def patch_execution(package: Path) -> None:
    path = package / "execution.py"
    text = path.read_text(encoding="utf-8")
    text = text.replace(
        "def run_area_reader(args, repo, out, area, repo_map, files):",
        "def run_area_reader(args, repo, out, area, repo_map, files, *, call_provider_fn=call_provider):",
    )
    text = text.replace("    raw, wall_seconds = call_provider(args, \"reader\", reader_prompt, args.reader_num_predict)", "    raw, wall_seconds = call_provider_fn(args, \"reader\", reader_prompt, args.reader_num_predict)")
    if "call_provider_fn" not in text:
        raise SystemExit("area-reader execution provider injection was not installed")
    path.write_text(text, encoding="utf-8")


def main() -> None:
    old_package = ROOT / "area_reader_v2"
    package = ROOT / "area_reader"
    if old_package.exists():
        move(old_package, package)
    if not package.is_dir():
        raise SystemExit("area_reader package not found")

    for source_name, target_name in RENAMES.items():
        move(package / source_name, package / target_name)

    core = package / "runner_core.py"
    if core.exists():
        core.unlink()

    for path in ROOT.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in TEXT_EXTENSIONS or ".git" in path.parts:
            continue
        try:
            original = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        updated = replace_all(original)
        if updated != original:
            path.write_text(updated, encoding="utf-8")

    patch_workflow(package)
    patch_pipeline(package)
    patch_execution(package)

    old_refs = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in TEXT_EXTENSIONS or ".git" in path.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if "area_reader_v2" in text or "runner_core" in text and "area_reader" in text:
            old_refs.append(str(path.relative_to(ROOT)))
    if old_refs:
        raise SystemExit("obsolete area-reader references remain: " + ", ".join(old_refs))


if __name__ == "__main__":
    main()
