#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Run one trusted issue-to-PR workflow without a hard-coded agent backend.

The default Run mode performs the deterministic workflow steps and invokes a
configurable agent command on each rendered prompt at the point where agent work
is needed. The default agent command is `codex exec`; override it with
--agent-command or AGENT_COMMAND to use another tool-capable prompt runner.
Raw Ollama models are supported through provider/model patch mode.

Usage:
  issue-to-pr-cycle.sh --env ENV_FILE [--mode Run] [options]

Modes:
  Run                        Prepare, plan, implement, check, PR/CI, verify, mark ready.
  Plan                       Prepare one issue and write plan.md with the planner agent.
  Prepare                    Select one ready issue and render planner.md.
  RenderImplementerPrompt    Render implementer.md after plan.md exists.
  LocalCheck                 Run local verification and render local-repair.md on failure.
  PrAndCi                    Commit via GitHub API, open/update PR, and watch CI.
  RenderVerificationRepair   Render verification-repair.md after verification-result.md fails.
  ReadyForReview             Mark the current issue ready for review.
  Blocked                    Mark the current issue blocked.

Options:
  --env FILE                  Project environment file. Required unless ENV_FILE is set.
  --owner OWNER               GitHub owner. Defaults to GITHUB_OWNER from env.
  --repo REPO                 GitHub repo. Defaults to GITHUB_REPO from env.
  --base BRANCH               Base branch. Defaults to BASE_BRANCH or main.
  --remote NAME               Remote name. Defaults to REMOTE_NAME or origin.
  --issue NUMBER              Prepare a specific issue instead of the next ready issue.
  --description TEXT          Use literal issue text instead of a GitHub issue.
  --description-file FILE     Read literal issue text from a file.
  --message TEXT              Blocked status message.
  --max-repair-attempts N     Repair attempts for local, CI, and verifier failures. Default: 3.
  --planner-provider NAME     Planner provider: command or ollama.
  --planner-model MODEL       Planner model. Implies --planner-provider ollama if omitted.
  --agent-provider NAME       Implementer/repair/verifier provider: command or ollama.
  --agent-model MODEL         Agent model. Implies --agent-provider ollama if omitted.
  --agent-command COMMAND     Prompt runner for command-mode implementation/repair/verification.
                               Default: AGENT_COMMAND or `codex exec`.
  --planner-agent-command CMD Prompt runner for command-mode planning. Defaults to
                               PLANNER_AGENT_COMMAND or --agent-command. Use
                               {prompt_file} or {prompt} placeholders for non-Codex runners.
EOF
}

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
automation_root="${AUTOMATION_ROOT:-$(cd -- "$script_dir/.." && pwd)}"
tool_root="${AUTODEV_ROOT:-$(cd -- "$script_dir/../.." && pwd)}"
prompt_runner="${PROMPT_RUNNER:-$tool_root/automation/prompt_runner.py}"
python_bin="${PYTHON:-python3}"
env_file="${ENV_FILE:-}"
mode="Run"
owner="${GITHUB_OWNER:-}"
repo="${GITHUB_REPO:-}"
base="${BASE_BRANCH:-main}"
remote="${REMOTE_NAME:-origin}"
issue="${ISSUE_NUMBER:-}"
description="${ISSUE_DESCRIPTION:-}"
description_file="${ISSUE_DESCRIPTION_FILE:-}"
message=""
agent_command="${AGENT_COMMAND:-codex exec}"
planner_agent_command="${PLANNER_AGENT_COMMAND:-}"
planner_provider="${PLANNER_PROVIDER:-}"
planner_model="${PLANNER_MODEL:-}"
agent_provider="${AGENT_PROVIDER:-}"
agent_model="${AGENT_MODEL:-}"
planner_provider_mode=false
agent_provider_mode=false
[[ -n "$planner_provider" ]] && planner_provider_mode=true
[[ -n "$agent_provider" ]] && agent_provider_mode=true
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
    --description) description="$2"; shift 2 ;;
    --description-file) description_file="$2"; shift 2 ;;
    --message) message="$2"; shift 2 ;;
    --max-repair-attempts) max_repair_attempts="$2"; shift 2 ;;
    --planner-provider) planner_provider="$2"; planner_provider_mode=true; shift 2 ;;
    --planner-model) planner_model="$2"; planner_provider_mode=true; shift 2 ;;
    --agent-provider) agent_provider="$2"; agent_provider_mode=true; shift 2 ;;
    --agent-model) agent_model="$2"; agent_provider_mode=true; shift 2 ;;
    --agent-command) agent_command="$2"; shift 2 ;;
    --planner-agent-command) planner_agent_command="$2"; shift 2 ;;
    --help|-h) usage; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; usage >&2; exit 2 ;;
  esac
done

[[ -n "$env_file" ]] || { echo "Missing --env or ENV_FILE" >&2; usage >&2; exit 2; }
[[ "$max_repair_attempts" =~ ^[0-9]+$ ]] || { echo "--max-repair-attempts must be a non-negative integer" >&2; exit 2; }
[[ -n "$planner_model" ]] && planner_provider_mode=true
[[ -n "$agent_model" ]] && agent_provider_mode=true
[[ -n "$planner_model" && -z "$planner_provider" ]] && planner_provider="ollama"
[[ -n "$agent_model" && -z "$agent_provider" ]] && agent_provider="ollama"
[[ -n "$planner_provider" ]] || planner_provider="command"
[[ -n "$agent_provider" ]] || agent_provider="command"
[[ "$planner_provider" == "command" || "$planner_provider" == "ollama" ]] || { echo "--planner-provider must be command or ollama" >&2; exit 2; }
[[ "$agent_provider" == "command" || "$agent_provider" == "ollama" ]] || { echo "--agent-provider must be command or ollama" >&2; exit 2; }
[[ "$planner_provider" != "ollama" || -n "$planner_model" ]] || { echo "--planner-provider ollama requires --planner-model" >&2; exit 2; }
[[ "$agent_provider" != "ollama" || -n "$agent_model" ]] || { echo "--agent-provider ollama requires --agent-model" >&2; exit 2; }
[[ -n "$planner_agent_command" ]] || planner_agent_command="$agent_command"

with_env=("$automation_root/scripts/with-env.sh" "$env_file")

run_prepare() {
  local args=("$automation_root/scripts/prepare-next-ready-issue.sh" --owner "$owner" --repo "$repo" --base "$base" --remote "$remote")
  [[ -n "$issue" ]] && args+=(--issue "$issue")
  [[ -n "$description" ]] && args+=(--description "$description")
  [[ -n "$description_file" ]] && args+=(--description-file "$description_file")
  if [[ "$planner_provider_mode" == true ]]; then
    args+=(--reader-provider "$planner_provider")
    [[ -n "$planner_model" ]] && args+=(--reader-model "$planner_model")
    [[ "$planner_provider" == "command" && -n "$planner_agent_command" ]] && args+=(--reader-command "$planner_agent_command")
  fi
  if [[ "$agent_provider_mode" == true ]]; then
    args+=(--coder-provider "$agent_provider")
    [[ -n "$agent_model" ]] && args+=(--coder-model "$agent_model")
    [[ "$agent_provider" == "command" && -n "$agent_command" ]] && args+=(--coder-command "$agent_command")
  fi
  "${with_env[@]}" "${args[@]}"
}

run_finalize() {
  "${with_env[@]}" "$automation_root/scripts/finalize-current-issue.sh" --mode "$1"
}

mark_blocked() {
  local reason="$1"
  "${with_env[@]}" "$automation_root/scripts/mark-current-issue.sh" --status Blocked --message "$reason" || true
}

shell_quote() {
  printf "%q" "$1"
}

run_agent_prompt() {
  local command="$1" prompt_file prompt rendered_command
  prompt_file="$(mktemp)"
  cat > "$prompt_file"
  prompt="$(cat "$prompt_file")"
  if [[ "$command" == *"{prompt_file}"* ]]; then
    rendered_command="${command//\{prompt_file\}/$(shell_quote "$prompt_file")}"
    "${with_env[@]}" bash -lc "$rendered_command"
  elif [[ "$command" == *"{prompt}"* ]]; then
    rendered_command="${command//\{prompt\}/$(shell_quote "$prompt")}"
    "${with_env[@]}" bash -lc "$rendered_command"
  else
    # Codex-compatible default: append the prompt as the final argv value.
    "${with_env[@]}" bash -lc "$command \"\$@\"" _ "$prompt"
  fi
  rm -f "$prompt_file"
}

run_provider_prompt() {
  local role="$1" provider="$2" model="$3" command="$4" output_file="${5:-}" commit_file="${6:-}" prompt_file args=()
  prompt_file="$(mktemp)"
  cat > "$prompt_file"
  args=("$prompt_runner" --role "$role" --provider "$provider" --prompt-file "$prompt_file")
  [[ -n "$model" ]] && args+=(--model "$model")
  [[ -n "$command" ]] && args+=(--command "$command")
  [[ -n "$output_file" ]] && args+=(--output-file "$output_file")
  [[ -n "$commit_file" ]] && args+=(--commit-message-file "$commit_file")
  if [[ -n "${PROMPT_RUNNER:-}" ]]; then
    "${with_env[@]}" "$python_bin" "${args[@]}"
  else
    "${with_env[@]}" env PYTHONPATH="$tool_root${PYTHONPATH:+:$PYTHONPATH}" "$python_bin" -m automation.prompt_runner "${args[@]:1}"
  fi
  rm -f "$prompt_file"
}

agent_write_plan() {
  if [[ "$planner_provider_mode" == true ]]; then
    run_provider_prompt planner "$planner_provider" "$planner_model" "$planner_agent_command" "$current_dir/plan.md" "" <<EOF
You are planning an AutoDev issue-to-PR run.

Return only the complete implementation plan as markdown. Do not edit files.

--- PLANNER PROMPT ---
$(cat "$current_dir/planner.md")
EOF
  else
    run_agent_prompt "$planner_agent_command" <<EOF
Use the issue-to-pr-automation skill.

Run the planner prompt below. Write your complete planner output to:

$current_dir/plan.md

Do not edit any other files.

--- PLANNER PROMPT ---
$(cat "$current_dir/planner.md")
EOF
  fi
  [[ -s "$current_dir/plan.md" ]] || { echo "Planner did not write $current_dir/plan.md" >&2; return 1; }
}

agent_implement() {
  if [[ "$agent_provider_mode" == true ]]; then
    run_provider_prompt implementer "$agent_provider" "$agent_model" "$agent_command" "" "$current_dir/commit-message.txt" <<EOF
You are implementing an AutoDev issue-to-PR task as a raw text model.

You cannot edit files directly. Return exactly one of these forms:

NO_CHANGES_REQUIRED

or:

COMMIT_MESSAGE: concise imperative commit message
BEGIN_UNIFIED_DIFF
<unified diff applicable with git apply>
END_UNIFIED_DIFF

--- IMPLEMENTER PROMPT ---
$(cat "$current_dir/implementer.md")
EOF
  else
    run_agent_prompt "$agent_command" <<EOF
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
  fi
  if [[ "$agent_provider_mode" != true ]]; then
    [[ -s "$current_dir/commit-message.txt" ]] || { echo "Implementer did not write $current_dir/commit-message.txt" >&2; return 1; }
  fi
}

agent_repair_file() {
  local prompt_file="$1"
  if [[ "$agent_provider_mode" == true ]]; then
    run_provider_prompt repair "$agent_provider" "$agent_model" "$agent_command" "" "" <<EOF
You are repairing an AutoDev issue-to-PR task as a raw text model.

You cannot edit files directly. Return exactly one of these forms:

NO_CHANGES_REQUIRED

or:

BEGIN_UNIFIED_DIFF
<unified diff applicable with git apply>
END_UNIFIED_DIFF

--- REPAIR PROMPT ---
$(cat "$prompt_file")
EOF
  else
    run_agent_prompt "$agent_command" <<EOF
Use the issue-to-pr-automation skill.

Run the repair prompt below. Fix only the failure described by the prompt, and edit the workspace directly.

--- REPAIR PROMPT ---
$(cat "$prompt_file")
EOF
  fi
}

agent_verify() {
  if [[ "$agent_provider_mode" == true ]]; then
    run_provider_prompt verifier "$agent_provider" "$agent_model" "$agent_command" "$current_dir/verification-result.md" "" <<EOF
You are verifying an AutoDev issue-to-PR task.

Return only the verification result. The first line must be exactly PASS or FAIL.

--- VERIFIER PROMPT ---
$(cat "$current_dir/verifier.md")
EOF
  else
    run_agent_prompt "$agent_command" <<EOF
Use the issue-to-pr-automation skill.

Run the verifier prompt below. Write only the verification result to:

$current_dir/verification-result.md

The file must start with exactly PASS or FAIL.

--- VERIFIER PROMPT ---
$(cat "$current_dir/verifier.md")
EOF
  fi
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
    agent_repair_file "$current_dir/local-repair.md"
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
    agent_repair_file "$current_dir/ci-repair.md"
    local_check_with_repairs
  done
}

prepare_and_plan() {
  [[ -n "$owner" ]] || { echo "Missing --owner or GITHUB_OWNER" >&2; exit 2; }
  [[ -n "$repo" ]] || { echo "Missing --repo or GITHUB_REPO" >&2; exit 2; }

  local prepare_log prepare_code restore_errexit=false
  prepare_log="$(mktemp)"
  case $- in *e*) restore_errexit=true; set +e ;; esac
  run_prepare | tee "$prepare_log"
  prepare_code=$?
  [[ "$restore_errexit" == true ]] && set -e
  if [[ $prepare_code -ne 0 ]]; then
    rm -f "$prepare_log"
    return "$prepare_code"
  fi
  if grep -q '^NO_READY_ISSUE$' "$prepare_log"; then
    rm -f "$prepare_log"
    return 2
  fi
  rm -f "$prepare_log"
  agent_write_plan || { mark_blocked "Planner did not produce plan.md."; return 1; }
}

run_full_cycle() {
  local verification_attempt=0 verification_first_line plan_code
  set +e
  prepare_and_plan
  plan_code=$?
  set -e
  [[ $plan_code -eq 2 ]] && return 0
  [[ $plan_code -eq 0 ]] || return "$plan_code"
  run_finalize RenderImplementerPrompt
  agent_implement || { mark_blocked "Implementer did not produce commit-message.txt."; return 1; }
  local_check_with_repairs || { mark_blocked "Automation could not complete after local repair attempts."; return 1; }

  while true; do
    pr_and_ci_with_repairs || { mark_blocked "Automation could not complete after CI repair attempts."; return 1; }
    agent_verify || { mark_blocked "Verifier did not produce verification-result.md."; return 1; }
    verification_first_line="$(head -n1 "$current_dir/verification-result.md" | tr -d '\r')"
    if [[ "$verification_first_line" == "PASS" ]]; then
      "${with_env[@]}" "$automation_root/scripts/mark-current-issue.sh" --status ReadyForReview
      return 0
    fi
    [[ "$verification_first_line" == "FAIL" ]] || { mark_blocked "Verifier result must start with PASS or FAIL."; return 1; }
    [[ $verification_attempt -lt $max_repair_attempts ]] || { mark_blocked "Automation could not complete after verification repair attempts."; return 1; }
    verification_attempt=$((verification_attempt + 1))
    run_finalize RenderVerificationRepair
    agent_repair_file "$current_dir/verification-repair.md"
    local_check_with_repairs || { mark_blocked "Automation could not complete after verification local-check repairs."; return 1; }
  done
}

case "$mode" in
  Run)
    run_full_cycle
    ;;
  Plan)
    set +e
    prepare_and_plan
    code=$?
    set -e
    [[ $code -eq 2 ]] && exit 0
    exit "$code"
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
