#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Run one trusted issue-to-PR workflow without nested prompt orchestration.

The default Run mode performs the deterministic workflow steps, invokes
`codex exec` on each rendered prompt at the point where agent work is needed,
and uses only the trusted scripts for GitHub issue/PR/CI state transitions.

Usage:
  issue-to-pr-cycle.sh --env ENV_FILE [--mode Run] [options]

Modes:
  Run                        Prepare, plan, implement, check, PR/CI, verify, mark ready.
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
  --max-repair-attempts N    Repair attempts for local, CI, and verifier failures. Default: 3.
EOF
}

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
automation_root="${AUTOMATION_ROOT:-$(cd -- "$script_dir/.." && pwd)}"
env_file="${ENV_FILE:-}"
mode="Run"
owner="${GITHUB_OWNER:-}"
repo="${GITHUB_REPO:-}"
base="${BASE_BRANCH:-main}"
remote="${REMOTE_NAME:-origin}"
issue="${ISSUE_NUMBER:-}"
message=""
max_repair_attempts="${MAX_REPAIR_ATTEMPTS:-3}"
current_dir=".codex-run/current"

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
    --max-repair-attempts) max_repair_attempts="$2"; shift 2 ;;
    --help|-h) usage; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; usage >&2; exit 2 ;;
  esac
done

[[ -n "$env_file" ]] || { echo "Missing --env or ENV_FILE" >&2; usage >&2; exit 2; }
[[ "$max_repair_attempts" =~ ^[0-9]+$ ]] || { echo "--max-repair-attempts must be a non-negative integer" >&2; exit 2; }

with_env=("$automation_root/scripts/with-env.sh" "$env_file")

run_prepare() {
  local args=("$automation_root/scripts/prepare-next-ready-issue.sh" --owner "$owner" --repo "$repo" --base "$base" --remote "$remote")
  [[ -n "$issue" ]] && args+=(--issue "$issue")
  "${with_env[@]}" "${args[@]}"
}

run_finalize() {
  "${with_env[@]}" "$automation_root/scripts/finalize-current-issue.sh" --mode "$1"
}

mark_blocked() {
  local reason="$1"
  "${with_env[@]}" "$automation_root/scripts/mark-current-issue.sh" --status Blocked --message "$reason" || true
}

run_codex_prompt() {
  local wrapper_file
  wrapper_file="$(mktemp)"
  cat > "$wrapper_file"
  "${with_env[@]}" codex exec "$(cat "$wrapper_file")"
  rm -f "$wrapper_file"
}

codex_write_plan() {
  run_codex_prompt <<EOF
Use the issue-to-pr-automation skill.

Run the planner prompt below. Write your complete planner output to:

$current_dir/plan.md

Do not edit any other files.

--- PLANNER PROMPT ---
$(cat "$current_dir/planner.md")
EOF
  [[ -s "$current_dir/plan.md" ]] || { echo "Planner did not write $current_dir/plan.md" >&2; return 1; }
}

codex_implement() {
  run_codex_prompt <<EOF
Use the issue-to-pr-automation skill.

Run the implementer prompt below. Edit the workspace directly.

Also write a concise commit message to:

$current_dir/commit-message.txt

Commit message rules:
- One short first line.
- Imperative mood.
- Mention the affected behavior or area.
- No markdown.
- No quotes around the message.

--- IMPLEMENTER PROMPT ---
$(cat "$current_dir/implementer.md")
EOF
  [[ -s "$current_dir/commit-message.txt" ]] || { echo "Implementer did not write $current_dir/commit-message.txt" >&2; return 1; }
}

codex_repair_file() {
  local prompt_file="$1"
  run_codex_prompt <<EOF
Use the issue-to-pr-automation skill.

Run the repair prompt below. Fix only the failure described by the prompt, and edit the workspace directly.

--- REPAIR PROMPT ---
$(cat "$prompt_file")
EOF
}

codex_verify() {
  run_codex_prompt <<EOF
Use the issue-to-pr-automation skill.

Run the verifier prompt below. Write only the verification result to:

$current_dir/verification-result.md

The file must start with exactly PASS or FAIL.

--- VERIFIER PROMPT ---
$(cat "$current_dir/verifier.md")
EOF
  [[ -s "$current_dir/verification-result.md" ]] || { echo "Verifier did not write $current_dir/verification-result.md" >&2; return 1; }
}

local_check_with_repairs() {
  local attempt=0 code
  while true; do
    set +e
    run_finalize LocalCheck
    code=$?
    set -e
    [[ $code -eq 0 ]] && return 0
    [[ $code -eq 10 && $attempt -lt $max_repair_attempts ]] || return "$code"
    attempt=$((attempt + 1))
    codex_repair_file "$current_dir/local-repair.md"
  done
}

pr_and_ci_with_repairs() {
  local attempt=0 code
  while true; do
    set +e
    run_finalize PrAndCi
    code=$?
    set -e
    [[ $code -eq 0 ]] && return 0
    [[ $code -eq 20 && $attempt -lt $max_repair_attempts ]] || return "$code"
    attempt=$((attempt + 1))
    codex_repair_file "$current_dir/ci-repair.md"
    local_check_with_repairs
  done
}

run_full_cycle() {
  [[ -n "$owner" ]] || { echo "Missing --owner or GITHUB_OWNER" >&2; exit 2; }
  [[ -n "$repo" ]] || { echo "Missing --repo or GITHUB_REPO" >&2; exit 2; }

  local prepare_log verification_attempt=0 verification_first_line
  prepare_log="$(mktemp)"
  run_prepare | tee "$prepare_log"
  if grep -q '^NO_READY_ISSUE$' "$prepare_log"; then
    rm -f "$prepare_log"
    return 0
  fi
  rm -f "$prepare_log"

  codex_write_plan || { mark_blocked "Planner did not produce plan.md."; return 1; }
  run_finalize RenderImplementerPrompt
  codex_implement || { mark_blocked "Implementer did not produce commit-message.txt."; return 1; }
  local_check_with_repairs || { mark_blocked "Automation could not complete after local repair attempts."; return 1; }

  while true; do
    pr_and_ci_with_repairs || { mark_blocked "Automation could not complete after CI repair attempts."; return 1; }
    codex_verify || { mark_blocked "Verifier did not produce verification-result.md."; return 1; }
    verification_first_line="$(head -n1 "$current_dir/verification-result.md" | tr -d '\r')"
    if [[ "$verification_first_line" == "PASS" ]]; then
      "${with_env[@]}" "$automation_root/scripts/mark-current-issue.sh" --status ReadyForReview
      return 0
    fi
    [[ "$verification_first_line" == "FAIL" ]] || { mark_blocked "Verifier result must start with PASS or FAIL."; return 1; }
    [[ $verification_attempt -lt $max_repair_attempts ]] || { mark_blocked "Automation could not complete after verification repair attempts."; return 1; }
    verification_attempt=$((verification_attempt + 1))
    run_finalize RenderVerificationRepair
    codex_repair_file "$current_dir/verification-repair.md"
    local_check_with_repairs || { mark_blocked "Automation could not complete after verification local-check repairs."; return 1; }
  done
}

case "$mode" in
  Run)
    run_full_cycle
    ;;
  Prepare)
    [[ -n "$owner" ]] || { echo "Missing --owner or GITHUB_OWNER" >&2; exit 2; }
    [[ -n "$repo" ]] || { echo "Missing --repo or GITHUB_REPO" >&2; exit 2; }
    run_prepare
    echo "NEXT_ACTION: If PREPARED was printed, read $current_dir/planner.md and write $current_dir/plan.md."
    ;;
  RenderImplementerPrompt)
    run_finalize RenderImplementerPrompt
    echo "NEXT_ACTION: Read $current_dir/implementer.md, implement directly, and write $current_dir/commit-message.txt."
    ;;
  LocalCheck)
    set +e; run_finalize LocalCheck; code=$?; set -e
    echo "NEXT_ACTION: If LOCAL_CHECK_FAILED was printed, read $current_dir/local-repair.md and fix only that failure."
    exit "$code"
    ;;
  PrAndCi)
    set +e; run_finalize PrAndCi; code=$?; set -e
    echo "NEXT_ACTION: If CI_PASSED was printed, read $current_dir/verifier.md and write $current_dir/verification-result.md. If CI_FAILED was printed, read $current_dir/ci-repair.md."
    exit "$code"
    ;;
  RenderVerificationRepair)
    run_finalize RenderVerificationRepair
    echo "NEXT_ACTION: Read $current_dir/verification-repair.md and fix only verifier gaps."
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
