#!/usr/bin/env bash
set -euo pipefail
source "${AUTOMATION_ROOT:-~/automation}/scripts/lib.sh"
STATUS=""; MESSAGE=""
while [[ $# -gt 0 ]]; do case "$1" in --status) STATUS="$2"; shift 2;; --message) MESSAGE="$2"; shift 2;; *) echo "Unknown arg: $1" >&2; exit 2;; esac; done
[[ -n "$STATUS" ]] || { echo "Missing --status" >&2; exit 2; }
require_cmd curl; require_cmd jq; init_gh_env; STATE=".codex-run/current/state.json"; [[ -f "$STATE" ]] || { echo "Missing state: $STATE" >&2; exit 1; }
[[ -n "${GH_TOKEN:-}" ]] || { echo "Missing GH_TOKEN" >&2; exit 1; }
github_api(){ local method="$1" path="$2" data="${3:-}" args; args=(--fail-with-body --silent --show-error --connect-timeout 5 --max-time 15 --retry 2 --retry-delay 2 --retry-max-time 45 --request "$method" --header "Authorization: Bearer $GH_TOKEN" --header "Accept: application/vnd.github+json" --header "X-GitHub-Api-Version: 2022-11-28"); [[ -n "$data" ]] && args+=(--header "Content-Type: application/json" --data "$data"); curl "${args[@]}" "https://api.github.com/$path"; }
issue="$(jq -r '.IssueNumber' "$STATE")"; repo="$(jq -r '.RepoFullName' "$STATE")"; pr_url="$(jq -r '.PrUrl // ""' "$STATE")"
case "$STATUS" in
  ReadyForReview)
    github_api DELETE "repos/$repo/issues/$issue/labels/codex%3Ain-progress" >/dev/null 2>&1 || true
    github_api DELETE "repos/$repo/issues/$issue/labels/codex%3Ablocked" >/dev/null 2>&1 || true
    github_api POST "repos/$repo/issues/$issue/labels" '{"labels":["codex:ready-for-review"]}' >/dev/null
    body="Codex automation completed.

PR:
$pr_url

Status:
Ready for review/merge.${MESSAGE:+

Notes:
$MESSAGE}"
    github_api POST "repos/$repo/issues/$issue/comments" "$(jq -n --arg body "$body" '{body:$body}')" >/dev/null
    jq '.Status="ReadyForReview"' "$STATE" > "$STATE.tmp" && mv "$STATE.tmp" "$STATE"
    echo "MARKED_READY_FOR_REVIEW"
    ;;
  Blocked)
    github_api DELETE "repos/$repo/issues/$issue/labels/codex%3Ain-progress" >/dev/null 2>&1 || true
    github_api POST "repos/$repo/issues/$issue/labels" '{"labels":["codex:blocked"]}' >/dev/null
    [[ -n "$MESSAGE" ]] || MESSAGE="Codex automation failed and needs manual review."
    body="Codex automation blocked.

Reason:

\`\`\`text
$MESSAGE
\`\`\`"
    github_api POST "repos/$repo/issues/$issue/comments" "$(jq -n --arg body "$body" '{body:$body}')" >/dev/null
    jq '.Status="Blocked"' "$STATE" > "$STATE.tmp" && mv "$STATE.tmp" "$STATE"
    echo "MARKED_BLOCKED"
    ;;
  *) echo "Unsupported status: $STATUS" >&2; exit 2;;
esac
