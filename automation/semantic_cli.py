from __future__ import annotations

import argparse
import sys
from pathlib import Path
from automation.provider_contract import ModelConfig, ModelProvider, ProviderError
from automation.provider_factory import load_provider_config

from automation.semantic_artifacts import (
    _write_result_pair,
)
from automation.semantic_configuration import (
    resolve_semantic_settings,
)
from automation.semantic_contract import (
    SemanticVerifierError,
)
from automation.semantic_invocation import (
    prepare_semantic_prompt,
    prepare_semantic_repair_prompt,
    resolve_profile_roles,
)
from automation.semantic_schema import (
    parse_semantic_output,
)

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build and validate AutoDev semantic verification artifacts."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    enabled = subparsers.add_parser("enabled")
    enabled.add_argument("--provider-profile", required=True)

    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--repo", required=True)
    prepare.add_argument("--current-dir", required=True)
    prepare.add_argument("--template", required=True)
    prepare.add_argument("--out", required=True)

    repair = subparsers.add_parser("repair-prompt")
    repair.add_argument("--repo", required=True)
    repair.add_argument("--current-dir", required=True)
    repair.add_argument("--template", required=True)
    repair.add_argument("--out", required=True)

    validate = subparsers.add_parser("validate")
    validate.add_argument("--input", required=True)
    validate.add_argument("--output", required=True)

    verdict = subparsers.add_parser("verdict")
    verdict.add_argument("--input", required=True)
    return parser

def run(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "enabled":
            file_config, roles = resolve_profile_roles(Path(args.provider_profile))
            settings = resolve_semantic_settings(
                file_config,
                verifier_configured=roles.get("verifier") is not None,
            )
            return 0 if settings.enabled else 1
        if args.command == "prepare":
            prepare_semantic_prompt(
                Path(args.repo),
                Path(args.current_dir),
                Path(args.template),
                Path(args.out),
            )
            return 0
        if args.command == "repair-prompt":
            prepare_semantic_repair_prompt(
                Path(args.repo),
                Path(args.current_dir),
                Path(args.template),
                Path(args.out),
            )
            return 0
        if args.command == "validate":
            result = parse_semantic_output(
                Path(args.input).read_text(encoding="utf-8")
            )
            output = Path(args.output)
            _write_result_pair(output, result, "Semantic Verification Result")
            return 0
        if args.command == "verdict":
            result = parse_semantic_output(
                Path(args.input).read_text(encoding="utf-8")
            )
            print(result["verdict"])
            return {"pass": 0, "repair": 10, "blocked": 20}[str(result["verdict"])]
    except (OSError, SemanticVerifierError, ProviderError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(run())
