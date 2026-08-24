from __future__ import annotations

from automation import opencode_resume_status

from automation import opencode_resume_execution

from automation import opencode_resume_checkpoint

import argparse
import json
import sys
from pathlib import Path
from automation import workflow_stages
from automation.model_providers import ProviderError, load_provider_config
from automation.prompt_runner import (
    REQUIRED_PLAN_HEADINGS,
    PromptRunnerError,
    handle_planner_output,
)
from automation.semantic_artifacts import write_final_verdict, write_semantic_result
from automation.semantic_contract import SemanticVerifierError
from automation.semantic_evidence import collect_changed_files, collect_cross_file_regression_evidence, collect_current_diff, collect_deterministic_evidence
from automation.semantic_prompts import build_schema_repair_prompt, build_semantic_prompt, extract_acceptance_criteria
from automation.semantic_schema import parse_semantic_output, semantic_result_template
from automation.semantic_text import render_template

from automation.opencode_adapter_contract import (
    AUTODEV_ROOT,
    COORDINATOR_STAGES,
    OpenCodeAdapterError,
    ROLE_NAMES,
)
from automation.opencode_adapter_models import (
    issue_number_from_arguments,
    render_model_mappings,
    resolve_opencode_model_mappings,
)
from automation.opencode_adapter_roles import (
    accept_role,
    prepare_role,
)
from automation.opencode_adapter_workflow import (
    workflow_stage,
)

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Thin OpenCode frontend for existing AutoDev role artifacts.")
    subparsers = parser.add_subparsers(dest="command", required=True)


    models = subparsers.add_parser("models")
    models.add_argument("--repo", default=".")

    status = subparsers.add_parser("status")
    status.add_argument("--repo", default=".")
    status.add_argument("--invalidate-role", action="append", choices=ROLE_NAMES, default=[])

    resume = subparsers.add_parser("resume")
    resume.add_argument("--repo", default=".")
    resume.add_argument("--invalidate-role", action="append", choices=ROLE_NAMES, default=[])

    prepare = subparsers.add_parser("prepare")
    prepare.add_argument(
        "--role",
        choices=ROLE_NAMES,
        required=True,
    )
    prepare.add_argument("--repo", default=".")
    prepare.add_argument("--arguments", default="")
    prepare.add_argument("--autodev-root", default=str(AUTODEV_ROOT))

    accept = subparsers.add_parser("accept")
    accept.add_argument(
        "--role",
        choices=ROLE_NAMES,
        required=True,
    )
    accept.add_argument("--repo", default=".")
    accept.add_argument("--input", default="")

    stage = subparsers.add_parser("stage")
    stage.add_argument("--name", choices=COORDINATOR_STAGES, required=True)
    stage.add_argument("--repo", default=".")
    stage.add_argument("--arguments", default="")
    stage.add_argument("--autodev-root", default=str(AUTODEV_ROOT))
    stage.add_argument("--attempt", type=int, default=0)
    stage.add_argument("--reason", default="")
    return parser

def run(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "install":
            from automation import opencode_install

            print(
                "DEPRECATED: `python -m automation.opencode_adapter install` is a compatibility shim; "
                "use `python -m automation.opencode_install` instead.",
                file=sys.stderr,
            )
            return opencode_install.run(
                [
                    "--target-repo",
                    args.target_repo,
                    "--autodev-root",
                    args.autodev_root,
                    "--python",
                    args.python,
                ]
            )
        if args.command == "models":
            mappings = resolve_opencode_model_mappings(Path(args.repo))
            print(render_model_mappings(mappings))
            return 0
        if args.command == "status":
            repo = Path(args.repo).expanduser().resolve()
            mappings = resolve_opencode_model_mappings(repo)
            print(
                opencode_resume_status.status_text(
                    repo,
                    mappings,
                    requested_invalidations=args.invalidate_role,
                ),
                end="",
            )
            return 0
        if args.command == "resume":
            repo = Path(args.repo).expanduser().resolve()
            mappings = resolve_opencode_model_mappings(repo)
            payload = opencode_resume_execution.resume(
                repo,
                mappings,
                invalidated_roles=set(args.invalidate_role),
            )
            print(json.dumps(payload, sort_keys=True))
            return 0
        if args.command == "prepare":
            path = prepare_role(
                args.role,
                Path(args.repo),
                args.arguments,
                autodev_root=Path(args.autodev_root),
            )
            print(path)
            return 0
        if args.command == "accept":
            paths = accept_role(
                args.role,
                Path(args.repo),
                Path(args.input) if args.input else None,
            )
            for path in paths:
                print(path)
            return 0
        if args.command == "stage":
            repo = Path(args.repo).expanduser().resolve()
            try:
                code, payload = workflow_stage(
                    args.name,
                    repo,
                    arguments=args.arguments,
                    autodev_root=Path(args.autodev_root),
                    attempt=args.attempt,
                    reason=args.reason,
                )
            except (
                OpenCodeAdapterError,
                PromptRunnerError,
                SemanticVerifierError,
                ProviderError,
                workflow_stages.WorkflowStageError,
                OSError,
                ValueError,
            ) as exc:
                payload = workflow_stages.record_stage_failure(
                    repo,
                    args.name,
                    exc,
                    requested_issue=issue_number_from_arguments(args.arguments),
                )
                opencode_resume_checkpoint.checkpoint_failure(repo, args.name, exc)
                print(json.dumps(payload, sort_keys=True))
                return 1
            print(json.dumps(payload, sort_keys=True))
            return code
    except (
        OpenCodeAdapterError,
        PromptRunnerError,
        SemanticVerifierError,
        ProviderError,
        workflow_stages.WorkflowStageError,
        OSError,
        ValueError,
    ) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 1

def main() -> int:
    return run()
