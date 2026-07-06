#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Run one trusted issue-to-PR workflow step without invoking codex exec.

This script turns the documented Codex Desktop workflow into scriptable steps.
It performs only trusted GitHub/state transitions; the Codex agent still reads
rendered prompts, edits the workspace, writes plan/commit/verification files,
and reruns this script for the next transition.

Usage:
  issue-to-pr-cycle.sh --env ENV_FILE --mode MODE [options]

Modes:
  Prepare                    Select one ready issue and render planner.md.
  RenderImplementerPrompt    Render implementer.md after plan.md exists.
  LocalCheck                 Run local verification and render local-repair.md on failure.
  PrAndCi                    Commit via GitHub API, open/update PR, and watch CI.
  RenderVerificationRepair   Render verification-repair.md after verification-result.md fails.
  ReadyForReview             Mark the current issue ready for review.
  Blocked                    Mark the current issue blocked.

Options:
  --env FILE                 Project environment file. Required unless ENV_FILE is set.
  --owner OWNER              GitHub owner. Defaults to GITHUB_OWNER from env.
  --repo REPO                GitHub repo. Defaults to GITHUB_REPO from env.
  --base BRANCH              Base branch. Defaults to BASE_BRANCH or main.
  --remote NAME              Remote name. Defaults to REMOTE_NAME or origin.
  --issue NUMBER             Prepare a specific issue instead of the next ready issue.
  --message TEXT             Blocked status message.

Codex-agent handoff:
  After Prepare: read .codex-run/current/planner.md and write plan.md.
  After RenderImplementerPrompt: read implementer.md, edit files, write commit-message.txt.
  After LOCAL_CHECK_FAILED: read local-repair.md and fix only that failure.
  After CI_FAILED: read ci-repair.md, fix only CI failure, rerun LocalCheck then PrAndCi.
  After CI_PASSED: read verifier.md and write verification-result.md with PASS or FAIL details.
EOF
}

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
automation_root="${AUTOMATION_ROOT:-$(cd -- "$script_dir/.." && pwd)}"
env_file="${ENV_FILE:-}"
mode=""
owner="${GITHUB_OWNER:-}"
repo="${GITHUB_REPO:-}"
base="${BASE_BRANCH:-main}"
remote="${REMOTE_NAME:-origin}"
issue="${ISSUE_NUMBER:-}"
message=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --env) env_file="$2"; shift 2 ;;
    --mode) mode="$2"; shift 2 ;;
    --owner) owner="$2"; shift 2 ;;
    --repo) repo="$2"; shift 2 ;;
    --base) base="$2"; shift 2 ;;
    --remote) remote="$2"; shift 2 ;;
    --issue) issue="$2"; shift 2 ;;
    --message) message="$2"; shift 2 ;;
    --help|-h) usage; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; usage >&2; exit 2 ;;
  esac
done

[[ -n "$mode" ]] || { echo "Missing --mode" >&2; usage >&2; exit 2; }
[[ -n "$env_file" ]] || { echo "Missing --env or ENV_FILE" >&2; usage >&2; exit 2; }

with_env=("$automation_root/scripts/with-env.sh" "$env_file")

run_prepare() {
  local args=("$automation_root/scripts/prepare-next-ready-issue.sh" --owner "$owner" --repo "$repo" --base "$base" --remote "$remote")
  [[ -n "$issue" ]] && args+=(--issue "$issue")
  "${with_env[@]}" "${args[@]}"
  cat <<'EOF'
NEXT_ACTION: If PREPARED was printed, read .codex-run/current/planner.md and write .codex-run/current/plan.md.
EOF
}

run_finalize() {
  "${with_env[@]}" "$automation_root/scripts/finalize-current-issue.sh" --mode "$1"
}

run_finalize_then_handoff() {
  local finalize_mode="$1"
  local handoff="$2"
  local code
  set +e
  "${with_env[@]}" "$automation_root/scripts/finalize-current-issue.sh" --mode "$finalize_mode"
  code=$?
  set -e
  echo "$handoff"
  exit "$code"
}

case "$mode" in
  Prepare)
    [[ -n "$owner" ]] || { echo "Missing --owner or GITHUB_OWNER" >&2; exit 2; }
    [[ -n "$repo" ]] || { echo "Missing --repo or GITHUB_REPO" >&2; exit 2; }
    run_prepare
    ;;
  RenderImplementerPrompt)
    run_finalize RenderImplementerPrompt
    echo "NEXT_ACTION: Read .codex-run/current/implementer.md, implement directly, and write .codex-run/current/commit-message.txt."
    ;;
  LocalCheck)
    run_finalize_then_handoff LocalCheck "NEXT_ACTION: If LOCAL_CHECK_FAILED was printed, read .codex-run/current/local-repair.md and fix only that failure."
    ;;
  PrAndCi)
    run_finalize_then_handoff PrAndCi "NEXT_ACTION: If CI_PASSED was printed, read .codex-run/current/verifier.md and write .codex-run/current/verification-result.md. If CI_FAILED was printed, read .codex-run/current/ci-repair.md."
    ;;
  RenderVerificationRepair)
    run_finalize RenderVerificationRepair
    echo "NEXT_ACTION: Read .codex-run/current/verification-repair.md and fix only verifier gaps."
    ;;
  ReadyForReview)
    "${with_env[@]}" "$automation_root/scripts/mark-current-issue.sh" --status ReadyForReview
    ;;
  Blocked)
    [[ -n "$message" ]] || message="Automation could not complete after repair attempts."
    "${with_env[@]}" "$automation_root/scripts/mark-current-issue.sh" --status Blocked --message "$message"
    ;;
  *)
    echo "Unknown mode: $mode" >&2
    usage >&2
    exit 2
    ;;
esac
