#!/usr/bin/env bash
set -euo pipefail
AUTOMATION_ROOT="${AUTOMATION_ROOT:-~/automation}"
LOG_DIR="$AUTOMATION_ROOT/logs"; STATE_DIR="$AUTOMATION_ROOT/state"; mkdir -p "$LOG_DIR" "$STATE_DIR"
ENV_FILE="${1:-${ENV_FILE:-$STATE_DIR/default.env}}"
[[ -f "$ENV_FILE" ]] || { echo "Environment file not found: $ENV_FILE" >&2; exit 2; }
# shellcheck disable=SC1090
source "$ENV_FILE"
: "${REPO_PATH:?REPO_PATH is required}"; : "${GITHUB_OWNER:?GITHUB_OWNER is required}"; : "${GITHUB_REPO:?GITHUB_REPO is required}"
BASE_BRANCH="${BASE_BRANCH:-main}"; REMOTE_NAME="${REMOTE_NAME:-origin}"; MAX_REPAIR_ATTEMPTS="${MAX_REPAIR_ATTEMPTS:-3}"; CODEX_TIMEOUT="${CODEX_TIMEOUT:-2h}"
ts="$(date -u +%Y%m%d-%H%M%S)"; log="$LOG_DIR/${GITHUB_REPO}-${ts}.log"; lock="$STATE_DIR/${GITHUB_REPO}.lock"
exec 9>"$lock"; flock -n 9 || { echo "Another run is active for $GITHUB_REPO" | tee -a "$log"; exit 0; }
cd "$REPO_PATH"
prompt_file="$(mktemp)"
cat > "$prompt_file" <<PROMPT
Use the issue-to-pr-automation skill.

Run exactly one issue-to-PR cycle in this repository.

Important:
- Do not call \`codex exec\`.
- You are the Codex agent. Do the planning, implementation, repair, and verification yourself.
- Never merge to main.
- Process only one issue.
- Keep all changes issue-scoped.
- Do not perform unrelated refactors.
- Do not use local git commands that write .git metadata.

Step 1 — Prepare
Run:
bash "$AUTOMATION_ROOT/scripts/prepare-next-ready-issue.sh" --owner "$GITHUB_OWNER" --repo "$GITHUB_REPO" --base "$BASE_BRANCH" --remote "$REMOTE_NAME"
If it prints NO_READY_ISSUE, stop.

Step 2 — Plan
Read .codex-run/current/planner.md and write the plan to .codex-run/current/plan.md.

Step 3 — Render implementer prompt
Run:
bash "$AUTOMATION_ROOT/scripts/finalize-current-issue.sh" --mode RenderImplementerPrompt

Step 4 — Implement
Read .codex-run/current/implementer.md. Implement directly in the workspace. Write a concise commit message to .codex-run/current/commit-message.txt.

Step 5 — Local check
Run:
bash "$AUTOMATION_ROOT/scripts/finalize-current-issue.sh" --mode LocalCheck
If LOCAL_CHECK_FAILED, read .codex-run/current/local-repair.md, fix only the local-check failure, rerun LocalCheck, repeat at most $MAX_REPAIR_ATTEMPTS times.

Step 6 — PR and CI
Run:
bash "$AUTOMATION_ROOT/scripts/finalize-current-issue.sh" --mode PrAndCi
If CI_FAILED, read .codex-run/current/ci-repair.md, fix only the CI failure, run LocalCheck again, rerun PrAndCi, repeat at most $MAX_REPAIR_ATTEMPTS times.

Step 7 — Verify
When PrAndCi prints CI_PASSED, read .codex-run/current/verifier.md. If PASS, write exactly PASS to .codex-run/current/verification-result.md. If FAIL, write FAIL plus concrete gaps.
If verification fails, run:
bash "$AUTOMATION_ROOT/scripts/finalize-current-issue.sh" --mode RenderVerificationRepair
Then read .codex-run/current/verification-repair.md, fix only verifier gaps, rerun LocalCheck and PrAndCi, and verify again.

Step 8 — Mark ready
When verification passes, run:
bash "$AUTOMATION_ROOT/scripts/mark-current-issue.sh" --status ReadyForReview
If you must give up, run:
bash "$AUTOMATION_ROOT/scripts/mark-current-issue.sh" --status Blocked --message "Automation could not complete after repair attempts."
PROMPT
echo "Starting Codex automation for $GITHUB_OWNER/$GITHUB_REPO at $ts" | tee -a "$log"
set +e
timeout "$CODEX_TIMEOUT" codex exec "$(cat "$prompt_file")" 2>&1 | tee -a "$log"
code=${PIPESTATUS[0]}
set -e
rm -f "$prompt_file"
echo "Finished with exit code $code" | tee -a "$log"
exit "$code"
