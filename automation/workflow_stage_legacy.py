from __future__ import annotations

import argparse
from pathlib import Path

from automation import workflow_stages
from automation.semantic_verifier import (
    extract_acceptance_criteria,
    parse_semantic_output,
    render_template,
)


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


def initialize_shipment_proof(
    repo: Path,
    current: Path,
    state: dict[str, object],
) -> dict[str, object]:
    if state.get("VerificationProofVersion"):
        return state
    base_sha = str(state.get("BaseSha", "")).strip()
    base_tree_sha = str(state.get("BaseTreeSha", "")).strip()
    if not base_sha or not base_tree_sha:
        raise workflow_stages.WorkflowStageError(
            "cannot initialize shipped-tree proof because prepared BaseSha/BaseTreeSha is missing"
        )
    local_head = workflow_stages.validate_prepared_worktree(repo, base_sha)
    snapshot_path = current / "workspace-snapshot.json"
    workflow_stages.write_workspace_snapshot(repo, snapshot_path)
    snapshot_hash = workflow_stages._file_sha256(snapshot_path)
    if not snapshot_hash:
        raise workflow_stages.WorkflowStageError(
            "cannot initialize shipped-tree proof because the workspace snapshot is missing"
        )
    state["VerificationProofVersion"] = workflow_stages.VERIFICATION_PROOF_VERSION
    state["PreparedLocalHeadSha"] = local_head
    state["PreparedSnapshotHash"] = snapshot_hash
    state["PrHeadSha"] = ""
    workflow_stages.write_state(current, state)
    workflow_stages._record_shipment_diagnostics(
        current,
        prepared_base_sha=base_sha,
        prepared_base_tree=base_tree_sha,
        prepared_local_head=local_head,
        prepared_snapshot_hash=snapshot_hash,
        proof_initialized_at="RenderImplementerPrompt",
    )
    return state


def sync_semantic_proof(
    current: Path,
    state: dict[str, object],
) -> dict[str, object]:
    verdict_path = current / "verification" / "final-verdict.json"
    if not verdict_path.is_file():
        return state
    issue_text = workflow_stages.read_text(current / "issue.md") or str(
        state.get("IssueText", "")
    )
    result = parse_semantic_output(
        workflow_stages.read_text(verdict_path),
        expected_criteria=extract_acceptance_criteria(issue_text) or None,
    )
    verdict = str(result["verdict"])
    state["LastSemanticVerdict"] = verdict
    if verdict == "pass":
        identity = str(state.get("VerifiedSourceIdentity", "")).strip()
        if state.get("VerificationProofVersion") and not identity:
            raise workflow_stages.WorkflowStageError(
                "semantic pass cannot be bound because the current local verification identity is missing"
            )
        state["SemanticSourceIdentity"] = identity
    else:
        state.pop("SemanticSourceIdentity", None)
    workflow_stages.write_state(current, state)
    return state


def run_mode(mode: str, repo: Path, autodev_root: Path) -> int:
    repo = repo.expanduser().resolve()
    autodev_root = autodev_root.expanduser().resolve()
    current = repo / workflow_stages.CURRENT_DIR
    state = workflow_stages.read_state(current)

    if mode == "RenderImplementerPrompt":
        state = initialize_shipment_proof(repo, current, state)
        workflow_stages.render_implementer_prompt(repo, current, state, autodev_root)
        print("RENDERED_IMPLEMENTER_PROMPT")
        return 0
    if mode == "LocalCheck":
        passed = workflow_stages.run_local_check(repo, current, state, autodev_root)
        print("LOCAL_CHECK_PASSED" if passed else "LOCAL_CHECK_FAILED")
        return 0 if passed else 10
    if mode == "PrAndCi":
        state = sync_semantic_proof(current, state)
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
