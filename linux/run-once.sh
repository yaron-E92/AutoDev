#!/usr/bin/env bash
set -euo pipefail

RUN_ONCE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
AUTOMATION_ROOT="${AUTOMATION_ROOT:-$HOME/automation}"
LOG_DIR="$AUTOMATION_ROOT/logs"
STATE_DIR="$AUTOMATION_ROOT/state"
mkdir -p "$LOG_DIR" "$STATE_DIR"

if [[ -x "$RUN_ONCE_DIR/scripts/issue-to-pr-cycle.sh" ]]; then
  ISSUE_TO_PR_SCRIPT="$RUN_ONCE_DIR/scripts/issue-to-pr-cycle.sh"
elif [[ -x "$RUN_ONCE_DIR/linux/scripts/issue-to-pr-cycle.sh" ]]; then
  ISSUE_TO_PR_SCRIPT="$RUN_ONCE_DIR/linux/scripts/issue-to-pr-cycle.sh"
else
  echo "Could not find issue-to-pr-cycle.sh relative to $RUN_ONCE_DIR" >&2
  exit 2
fi

ENV_FILE="${1:-${ENV_FILE:-$STATE_DIR/default.env}}"
[[ -f "$ENV_FILE" ]] || { echo "Environment file not found: $ENV_FILE" >&2; exit 2; }

# shellcheck disable=SC1090
source "$ENV_FILE"
: "${GITHUB_OWNER:?GITHUB_OWNER is required}"
: "${GITHUB_REPO:?GITHUB_REPO is required}"

BASE_BRANCH="${BASE_BRANCH:-main}"
REMOTE_NAME="${REMOTE_NAME:-origin}"
ts="$(date -u +%Y%m%d-%H%M%S)"
log="$LOG_DIR/${GITHUB_REPO}-${ts}.log"
lock="$STATE_DIR/${GITHUB_REPO}.lock"

exec 9>"$lock"
flock -n 9 || { echo "Another run is active for $GITHUB_REPO" | tee -a "$log"; exit 0; }

{
  echo "Running one Codex issue-to-PR cycle for $GITHUB_OWNER/$GITHUB_REPO at $ts"
  echo "This script runs deterministic steps and invokes the configured agent command on each rendered prompt."
  "$ISSUE_TO_PR_SCRIPT" \
    --env "$ENV_FILE" \
    --mode Run \
    --owner "$GITHUB_OWNER" \
    --repo "$GITHUB_REPO" \
    --base "$BASE_BRANCH" \
    --remote "$REMOTE_NAME"
} 2>&1 | tee -a "$log"
