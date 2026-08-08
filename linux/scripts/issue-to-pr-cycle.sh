#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Run one trusted issue-to-PR workflow without a hard-coded agent backend.
Provider resolution is delegated to Python.

Usage:
  issue-to-pr-cycle.sh --env ENV_FILE [--mode Run] [options]

Modes:
  Run                        Prepare, plan, implement, check, semantic verify, PR/CI, mark ready.
  Plan                       Prepare one issue and write plan.md with the planner agent.
  Prepare                    Select one ready issue and render planner.md.
  Preflight                  Validate the configured provider profile without repository mutation.
  RenderImplementerPrompt    Render implementer.md after plan.md exists.
  LocalCheck                 Run local verification and render local-repair.md on failure.
  PrAndCi                    Commit via GitHub API, open/update PR, and watch CI.
  RenderVerificationRepair   Render verification-repair.md after verification-result.md fails.
  ReadyForReview             Mark the current issue ready for review.
  Blocked                    Mark the current issue blocked.

Options:
  --env FILE                  Project environment file. Required unless ENV_FILE is set.
  --provider-profile FILE     Version-2 role provider profile.
  --provider-preflight-out F  Preflight JSON path. Default: .autodev-run/provider-preflight.json.
  --disable-semantic-verification
                              Preserve the legacy PASS/FAIL verifier workflow.
  --max-semantic-repair-attempts N
                              Targeted semantic repairs. Default: 1.
  --owner OWNER               GitHub owner. Defaults to GITHUB_OWNER.
  --repo REPO                 GitHub repo. Defaults to GITHUB_REPO.
  --base BRANCH               Base branch. Defaults to BASE_BRANCH or main.
  --remote NAME               Remote name. Defaults to REMOTE_NAME or origin.
  --issue NUMBER              Prepare a specific issue instead of the next ready issue.
  --description TEXT          Use literal issue text instead of a GitHub issue.
  --description-file FILE     Read literal issue text from a file.
  --message TEXT              Blocked status message.
  --max-repair-attempts N     Deterministic/CI repair attempts. Default: 3.
  --planner-provider NAME     Legacy planner transport override; Python validates it.
  --planner-model MODEL       Legacy planner model override.
  --agent-provider NAME       Legacy implementer/fixer/verifier transport override.
  --agent-model MODEL         Legacy agent model override.
  --agent-command COMMAND     Direct command when no provider profile is selected.
  --planner-agent-command CMD Direct planner command when no provider profile is selected.
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
provider_profile="${PROVIDER_PROFILE:-}"
provider_preflight_out="${PROVIDER_PREFLIGHT_OUT:-.autodev-run/provider-preflight.json}"
disable_semantic_verification="${DISABLE_SEMANTIC_VERIFICATION:-0}"
max_semantic_repair_attempts="${MAX_SEMANTIC_REPAIR_ATTEMPTS:-1}"
agent_command="${AGENT_COMMAND:-codex exec}"
planner_agent_command="${PLANNER_AGENT_COMMAND:-}"
planner_provider="${PLANNER_PROVIDER:-}"
planner_model="${PLANNER_MODEL:-}"
agent_provider="${AGENT_PROVIDER:-}"
agent_model="${AGENT_MODEL:-}"
max_repair_attempts="${MAX_REPAIR_ATTEMPTS:-3}"
current_dir=".autodev-run/current"
telemetry_file="$current_dir/model-invocations.json"
semantic_enabled=false

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
    --provider-profile) provider_profile="$2"; shift 2 ;;
    --provider-preflight-out) provider_preflight_out="$2"; shift 2 ;;
    --disable-semantic-verification) disable_semantic_verification=1; shift ;;
    --max-semantic-repair-attempts) max_semantic_repair_attempts="$2"; shift 2 ;;
    --max-repair-attempts) max_repair_attempts="$2"; shift 2 ;;
    --planner-provider) planner_provider="$2"; shift 2 ;;
    --planner-model) planner_model="$2"; shift 2 ;;
    --agent-provider) agent_provider="$2"; shift 2 ;;
    --agent-model) agent_model="$2"; shift 2 ;;
    --agent-command) agent_command="$2"; shift 2 ;;
    --planner-agent-command) planner_agent_command="$2"; shift 2 ;;
    --help|-h) usage; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; usage >&2; exit 2 ;;
  esac
done

[[ -n "$env_file" ]] || { echo "Missing --env or ENV_FILE" >&2; usage >&2; exit 2; }
[[ "$max_repair_attempts" =~ ^[0-9]+$ ]] || { echo "--max-repair-attempts must be a non-negative integer" >&2; exit 2; }
[[ "$max_semantic_repair_attempts" =~ ^[0-9]+$ ]] || {
  echo "--max-semantic-repair-attempts must be a non-negative integer" >&2
  exit 2
}
[[ -n "$planner_agent_command" ]] || planner_agent_command="$agent_command"
planner_provider_mode=false
agent_provider_mode=false
[[ -n "$provider_profile$planner_provider$planner_model" ]] && planner_provider_mode=true
[[ -n "$provider_profile$agent_provider$agent_model" ]] && agent_provider_mode=true
with_env=("$automation_root/scripts/with-env.sh" "$env_file")

run_python_module() {
  local module="$1"; shift
  "${with_env[@]}" env PYTHONPATH="$tool_root${PYTHONPATH:+:$PYTHONPATH}" "$python_bin" -m "$module" "$@"
}

resolve_semantic_mode() {
  semantic_enabled=false
  [[ "$disable_semantic_verification" != 1 ]] || return 0
  [[ -n "$provider_profile" ]] || return 0

  local code
  set +e
  run_python_module automation.semantic_verifier enabled --provider-profile "$provider_profile"
  code=$?
  set -e
  if [[ $code -eq 0 ]]; then
    semantic_enabled=true
    return 0
  fi
  [[ $code -eq 1 ]] && return 0
  return "$code"
}

run_prepare() {
  local args=("$automation_root/scripts/prepare-next-ready-issue.sh" --owner "$owner" --repo "$repo" --base "$base" --remote "$remote")
  [[ -n "$issue" ]] && args+=(--issue "$issue")
  [[ -n "$description" ]] && args+=(--description "$description")
  [[ -n "$description_file" ]] && args+=(--description-file "$description_file")
  [[ -n "$provider_profile" ]] && args+=(--provider-profile "$provider_profile")
  if [[ "$planner_provider_mode" == true && -z "$provider_profile" ]]; then
    [[ -n "$planner_provider" ]] && args+=(--reader-provider "$planner_provider")
    [[ -n "$planner_model" ]] && args+=(--reader-model "$planner_model")
    [[ "$planner_provider" == "command" && -n "$planner_agent_command" ]] && args+=(--reader-command "$planner_agent_command")
  fi
  if [[ "$agent_provider_mode" == true && -z "$provider_profile" ]]; then
    [[ -n "$agent_provider" ]] && args+=(--coder-provider "$agent_provider")
    [[ -n "$agent_model" ]] && args+=(--coder-model "$agent_model")
    [[ "$agent_provider" == "command" && -n "$agent_command" ]] && args+=(--coder-command "$agent_command")
  fi
  "${with_env[@]}" "${args[@]}"
}

run_finalize() {
  "${with_env[@]}" "$automation_root/scripts/finalize-current-issue.sh" --mode "$1"
}

mark_status() {
  local status="$1" reason="${2:-}"
  local args=("$automation_root/scripts/mark-current-issue.sh" --status "$status")
  [[ -n "$reason" ]] && args+=(--message "$reason")
  "${with_env[@]}" "${args[@]}" || true
}

shell_quote() { printf "%q" "$1"; }

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
    "${with_env[@]}" bash -lc "$command \"\$@\"" _ "$prompt"
  fi
  rm -f "$prompt_file"
}

run_provider_prompt() {
  local role="$1" output_file="${2:-}" commit_file="${3:-}" verifier_format="${4:-}" prompt_file args=()
  prompt_file="$(mktemp)"
  cat > "$prompt_file"
  args=(--role "$role" --prompt-file "$prompt_file" --telemetry-file "$telemetry_file")
  [[ -n "$provider_profile" ]] && args+=(--provider-profile "$provider_profile")

  local legacy_provider legacy_model legacy_command
  if [[ "$role" == "planner" ]]; then
    legacy_provider="$planner_provider"; legacy_model="$planner_model"; legacy_command="$planner_agent_command"
  else
    legacy_provider="$agent_provider"; legacy_model="$agent_model"; legacy_command="$agent_command"
  fi
  if [[ -n "$legacy_model" && ( -z "$legacy_provider" || "$legacy_provider" == "ollama" ) ]]; then
    legacy_command=""
  fi
  [[ -n "$legacy_provider" ]] && args+=(--provider "$legacy_provider")
  [[ -n "$legacy_model" ]] && args+=(--model "$legacy_model")
  [[ -z "$provider_profile" && -n "$legacy_command" ]] && args+=(--command "$legacy_command")
  [[ -n "$output_file" ]] && args+=(--output-file "$output_file")
  [[ -n "$commit_file" ]] && args+=(--commit-message-file "$commit_file")
  [[ -n "$verifier_format" ]] && args+=(--verifier-format "$verifier_format")

  if [[ -n "${PROMPT_RUNNER:-}" ]]; then
    "${with_env[@]}" "$python_bin" "$prompt_runner" "${args[@]}"
  else
    run_python_module automation.prompt_runner "${args[@]}"
  fi
  rm -f "$prompt_file"
}

run_provider_preflight() {
  [[ -n "$provider_profile" ]] || {
    echo "Preflight requires --provider-profile or PROVIDER_PROFILE" >&2
    return 2
  }
  run_python_module automation.provider_preflight \
    --provider-profile "$provider_profile" \
    --out "$provider_preflight_out"
}

agent_write_plan() {
  if [[ "$planner_provider_mode" == true ]]; then
    run_provider_prompt planner "$current_dir/plan.md" "" < "$current_dir/planner.md"
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
  [[ -s "$current_dir/plan.md" ]] || {
    echo "Planner did not write $current_dir/plan.md" >&2
    return 1
  }
}

agent_implement() {
  if [[ "$agent_provider_mode" == true ]]; then
    run_provider_prompt implementer "" "$current_dir/commit-message.txt" < "$current_dir/implementer.md"
  else
    run_agent_prompt "$agent_command" <<EOF
Use the issue-to-pr-automation skill.

Run the implementer prompt below. Edit the workspace directly.
Write a concise imperative commit message to $current_dir/commit-message.txt.

--- IMPLEMENTER PROMPT ---
$(cat "$current_dir/implementer.md")
EOF
  fi
  [[ -s "$current_dir/commit-message.txt" ]] || {
    echo "Implementer did not write $current_dir/commit-message.txt" >&2
    return 1
  }
}

agent_repair_file() {
  local prompt_file="$1"
  if [[ "$agent_provider_mode" == true ]]; then
    run_provider_prompt fixer "" "" < "$prompt_file"
  else
    run_agent_prompt "$agent_command" <<EOF
Use the issue-to-pr-automation skill.

Run the repair prompt below. Fix only the described failure and edit the workspace directly.

--- REPAIR PROMPT ---
$(cat "$prompt_file")
EOF
  fi
}

agent_verify_legacy() {
  if [[ "$agent_provider_mode" == true ]]; then
    run_provider_prompt verifier "$current_dir/verification-result.md" "" < "$current_dir/verifier.md"
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
  [[ -s "$current_dir/verification-result.md" ]] || {
    echo "Verifier did not write $current_dir/verification-result.md" >&2
    return 1
  }
}

prepare_semantic_prompt() {
  mkdir -p "$current_dir/verification"
  run_python_module automation.semantic_verifier prepare \
    --repo . \
    --current-dir "$current_dir" \
    --template "$tool_root/promptTemplates/verifier.md" \
    --out "$current_dir/verifier.md"
}

prepare_semantic_repair_prompt() {
  run_python_module automation.semantic_verifier repair-prompt \
    --repo . \
    --current-dir "$current_dir" \
    --template "$tool_root/promptTemplates/verification-repair.md" \
    --out "$current_dir/verification-repair.md"
}

agent_verify_semantic() {
  run_provider_prompt verifier "$current_dir/verification-result.json" "" semantic-json < "$current_dir/verifier.md"
  [[ -s "$current_dir/verification-result.json" ]] || {
    echo "Semantic verifier did not write $current_dir/verification-result.json" >&2
    return 1
  }
}

semantic_verdict() {
  local code
  set +e
  run_python_module automation.semantic_verifier verdict --input "$current_dir/verification-result.json"
  code=$?
  set -e
  return "$code"
}

semantic_gate() {
  [[ "$semantic_enabled" == true ]] || return 0

  local verdict_code
  prepare_semantic_prompt
  agent_verify_semantic
  cp "$current_dir/verification-result.json" "$current_dir/verification/semantic-attempt-0.json"

  set +e
  semantic_verdict
  verdict_code=$?
  set -e

  if [[ $verdict_code -eq 0 ]]; then
    cp "$current_dir/verification-result.json" "$current_dir/verification/final-verdict.json"
    return 0
  fi
  if [[ $verdict_code -eq 20 ]]; then
    cp "$current_dir/verification-result.json" "$current_dir/verification/final-verdict.json"
    echo "Semantic verifier blocked the run." >&2
    return 1
  fi
  if [[ $verdict_code -ne 10 ]]; then
    echo "Semantic verifier failed or returned malformed output." >&2
    return 1
  fi
  if [[ $max_semantic_repair_attempts -lt 1 ]]; then
    cp "$current_dir/verification-result.json" "$current_dir/verification/final-verdict.json"
    echo "Semantic verifier requested repair but semantic repair is disabled." >&2
    return 1
  fi

  prepare_semantic_repair_prompt
  agent_repair_file "$current_dir/verification-repair.md"
  local_check_with_repairs || return $?

  prepare_semantic_prompt
  agent_verify_semantic
  cp "$current_dir/verification-result.json" "$current_dir/verification/semantic-attempt-1.json"

  set +e
  semantic_verdict
  verdict_code=$?
  set -e
  cp "$current_dir/verification-result.json" "$current_dir/verification/final-verdict.json"
  [[ $verdict_code -eq 0 ]] || {
    echo "Semantic verification did not pass after the targeted repair." >&2
    return 1
  }
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
    local_check_with_repairs || return $?
    semantic_gate || return $?
  done
}

prepare_and_plan() {
  [[ -n "$owner" ]] || { echo "Missing --owner or GITHUB_OWNER" >&2; return 2; }
  [[ -n "$repo" ]] || { echo "Missing --repo or GITHUB_REPO" >&2; return 2; }
  local prepare_log prepare_code
  prepare_log="$(mktemp)"
  set +e
  run_prepare | tee "$prepare_log"
  prepare_code=${PIPESTATUS[0]}
  set -e
  if [[ $prepare_code -ne 0 ]]; then rm -f "$prepare_log"; return "$prepare_code"; fi
  if grep -q '^NO_READY_ISSUE$' "$prepare_log"; then rm -f "$prepare_log"; return 2; fi
  rm -f "$prepare_log"
  agent_write_plan
}

legacy_verification_loop() {
  local verification_attempt=0 first_line code
  while true; do
    agent_verify_legacy || {
      mark_status Blocked "Verifier did not produce verification-result.md."
      return 1
    }
    first_line="$(head -n 1 "$current_dir/verification-result.md" | tr -d '\r')"
    [[ "$first_line" == PASS ]] && { mark_status ReadyForReview; return 0; }
    [[ "$first_line" == FAIL ]] || {
      mark_status Blocked "Verifier result must start with PASS or FAIL."
      return 1
    }
    [[ $verification_attempt -lt $max_repair_attempts ]] || {
      mark_status Blocked "Automation could not complete after verification repair attempts."
      return 1
    }
    verification_attempt=$((verification_attempt + 1))
    run_finalize RenderVerificationRepair
    agent_repair_file "$current_dir/verification-repair.md"
    local_check_with_repairs || {
      code=$?
      mark_status Blocked "Automation could not complete after verification local-check repairs."
      return "$code"
    }
    pr_and_ci_with_repairs || {
      code=$?
      mark_status Blocked "Automation could not complete after CI repair attempts."
      return "$code"
    }
  done
}

run_cycle() {
  local code
  resolve_semantic_mode || return $?

  set +e
  prepare_and_plan
  code=$?
  set -e
  [[ $code -eq 2 ]] && return 0
  [[ $code -eq 0 ]] || return "$code"

  run_finalize RenderImplementerPrompt
  agent_implement || {
    mark_status Blocked "Implementer did not produce commit-message.txt."
    return 1
  }
  local_check_with_repairs || {
    code=$?
    mark_status Blocked "Automation could not complete after local repair attempts."
    return "$code"
  }
  semantic_gate || {
    code=$?
    mark_status Blocked "Independent semantic verification did not pass."
    return "$code"
  }
  pr_and_ci_with_repairs || {
    code=$?
    mark_status Blocked "Automation could not complete after CI repair attempts."
    return "$code"
  }

  if [[ "$semantic_enabled" == true ]]; then
    mark_status ReadyForReview
    return 0
  fi
  legacy_verification_loop
}

case "$mode" in
  Run) run_cycle ;;
  Plan) set +e; prepare_and_plan; code=$?; set -e; [[ $code -eq 2 ]] && exit 0; exit "$code" ;;
  Prepare) run_prepare ;;
  Preflight) run_provider_preflight ;;
  RenderImplementerPrompt) run_finalize RenderImplementerPrompt ;;
  LocalCheck) run_finalize LocalCheck ;;
  PrAndCi) run_finalize PrAndCi ;;
  RenderVerificationRepair) run_finalize RenderVerificationRepair ;;
  ReadyForReview) mark_status ReadyForReview "$message" ;;
  Blocked) mark_status Blocked "${message:-Automation could not complete after repair attempts.}" ;;
  *) echo "Unsupported mode: $mode" >&2; usage >&2; exit 2 ;;
esac
