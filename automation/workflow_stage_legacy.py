from __future__ import annotations

import argparse
from pathlib import Path

from automation import workflow_stages
from automation.semantic_verifier import render_template


MODES = (
    "RenderImplementerPrompt",
    "LocalCheck",
    "PrAndCi",
    "RenderVerificationRepair",
)


def render_verification_repair(
    current: Path,
    state: dict[str, object],
    autodev_root: Path,
) -> None:
    result_path = current / "verification-result.md"
    if not result_path.is_file():
        raise workflow_stages.WorkflowStageError(
            "cannot render verification repair prompt because verification-result.md is missing"
        )
    prompt = render_template(
        workflow_stages.read_text(autodev_root / "promptTemplates" / "verification-repair.md"),
        {
            "IssueText": workflow_stages.read_text(current / "issue.md")
            or str(state.get("IssueText", "")),
            "Plan": workflow_stages.read_text(current / "plan.md"),
            "VerificationFailure": workflow_stages.read_text(result_path),
            "LocalCheck": str(state.get("LocalCheck", "")),
            "StackContext": str(state.get("StackContext", "")),
        },
    )
    workflow_stages.write_text(current / "verification-repair.md", prompt)
    state["Status"] = "VerificationRepairPromptRendered"
    workflow_stages.write_state(current, state)


def run_mode(mode: str, repo: Path, autodev_root: Path) -> int:
    repo = repo.expanduser().resolve()
    autodev_root = autodev_root.expanduser().resolve()
    current = repo / workflow_stages.CURRENT_DIR
    state = workflow_stages.read_state(current)

    if mode == "RenderImplementerPrompt":
        workflow_stages.render_implementer_prompt(repo, current, state, autodev_root)
        print("RENDERED_IMPLEMENTER_PROMPT")
        return 0
    if mode == "LocalCheck":
        passed = workflow_stages.run_local_check(repo, current, state, autodev_root)
        print("LOCAL_CHECK_PASSED" if passed else "LOCAL_CHECK_FAILED")
        return 0 if passed else 10
    if mode == "PrAndCi":
        passed = workflow_stages.pr_and_ci(repo, current, state, autodev_root)
        print("CI_PASSED" if passed else "CI_FAILED")
        return 0 if passed else 20
    if mode == "RenderVerificationRepair":
        render_verification_repair(current, state, autodev_root)
        print("RENDERED_VERIFICATION_REPAIR")
        return 0
    raise workflow_stages.WorkflowStageError(f"unsupported legacy finalize mode: {mode}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compatibility CLI for existing AutoDev finalize frontends.")
    parser.add_argument("--mode", choices=MODES, required=True)
    parser.add_argument("--repo", default=".")
    parser.add_argument("--autodev-root", default=str(workflow_stages.AUTODEV_ROOT))
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        return run_mode(args.mode, Path(args.repo), Path(args.autodev_root))
    except (workflow_stages.WorkflowStageError, OSError, ValueError) as exc:
        print(str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
