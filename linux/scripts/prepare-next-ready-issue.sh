#!/usr/bin/env bash
set -euo pipefail
source "${AUTOMATION_ROOT:-~/automation}/scripts/lib.sh"
OWNER="${GITHUB_OWNER:-}"; REPO="${GITHUB_REPO:-}"; BASE="${BASE_BRANCH:-main}"; REMOTE="${REMOTE_NAME:-origin}"; ISSUE="${ISSUE_NUMBER:-}"; PROFILES="${PROFILES:-}"; LOCAL_CHECK="${LOCAL_CHECK:-}"; STACK_CONTEXT="${STACK_CONTEXT:-}"; FORCE_CURRENT="${FORCE_CURRENT:-false}"
while [[ $# -gt 0 ]]; do case "$1" in --owner) OWNER="$2"; shift 2;; --repo) REPO="$2"; shift 2;; --base) BASE="$2"; shift 2;; --remote) REMOTE="$2"; shift 2;; --issue) ISSUE="$2"; shift 2;; --profiles) PROFILES="$2"; shift 2;; --local-check) LOCAL_CHECK="$2"; shift 2;; --stack-context) STACK_CONTEXT="$2"; shift 2;; --force-current) FORCE_CURRENT=true; shift;; *) echo "Unknown arg: $1" >&2; exit 2;; esac; done
require_cmd gh; require_cmd jq; require_cmd sha256sum; require_cmd python3; init_gh_env
FULL="$(repo_full_name "$OWNER" "$REPO")"; RUN_ROOT=".codex-run"; CURRENT="$RUN_ROOT/current"; mkdir -p "$RUN_ROOT"
if [[ -d "$CURRENT" ]]; then if [[ "$FORCE_CURRENT" == true ]]; then rm -rf "$CURRENT"; else mv "$CURRENT" "$RUN_ROOT/archive-$(date -u +%Y%m%d-%H%M%S)"; fi; fi; mkdir -p "$CURRENT"
if [[ -n "$ISSUE" ]]; then issue_json="$(gh issue view "$ISSUE" --repo "$FULL" --json number,title,body,url,labels)"; else next_number="$(gh issue list --repo "$FULL" --state open --label "codex:ready" --json number,title,labels --limit 50 | jq -r '[.[] | select([.labels[].name] | index("codex:in-progress") | not)][0].number // empty')"; [[ -z "$next_number" ]] && { echo "NO_READY_ISSUE"; exit 0; }; issue_json="$(gh issue view "$next_number" --repo "$FULL" --json number,title,body,url,labels)"; fi
issue_number="$(jq -r '.number' <<< "$issue_json")"; issue_title="$(jq -r '.title' <<< "$issue_json")"; issue_url="$(jq -r '.url' <<< "$issue_json")"; labels_json="$(jq '[.labels[].name]' <<< "$issue_json")"
echo "Selected issue #$issue_number: $issue_title"
cleanup_on_fail(){ code=$?; if [[ $code -ne 0 && -n "${issue_number:-}" ]]; then gh issue edit "$issue_number" --repo "$FULL" --remove-label "codex:in-progress" --add-label "codex:blocked" >/dev/null 2>&1 || true; gh issue comment "$issue_number" --repo "$FULL" --body "Codex automation prepare step failed. Check automation logs." >/dev/null 2>&1 || true; fi; exit $code; }
trap cleanup_on_fail ERR
gh issue edit "$issue_number" --repo "$FULL" --add-label "codex:in-progress" >/dev/null
resolved="$(resolve_profiles_json "$labels_json" "$PROFILES" "$LOCAL_CHECK" "$STACK_CONTEXT")"; profiles_csv="$(jq -r '.profilesCsv' <<< "$resolved")"; local_check="$(jq -r '.localCheck' <<< "$resolved")"; stack_context="$(jq -r '.stackContext' <<< "$resolved")"
base_ref="$(gh api "repos/$FULL/git/ref/heads/$BASE")"; base_sha="$(jq -r '.object.sha' <<< "$base_ref")"; base_commit="$(gh api "repos/$FULL/git/commits/$base_sha")"; base_tree_sha="$(jq -r '.tree.sha' <<< "$base_commit")"
slug="$(safe_slug "issue-$issue_number-$issue_title")"; branch_name="codex/$slug-$(date -u +%Y%m%d-%H%M%S)"; body="$(jq -r '.body // ""' <<< "$issue_json")"
cat > "$CURRENT/issue.md" <<ISSUEEOF
# GitHub Issue #$issue_number: $issue_title

URL: $issue_url

$body
ISSUEEOF
write_workspace_snapshot "$CURRENT/workspace-snapshot.json"
printf '%s' "$(cat "$CURRENT/issue.md")" > "$CURRENT/.issue.tmp"; printf '%s' "$local_check" > "$CURRENT/.local-check.tmp"; printf '%s' "$stack_context" > "$CURRENT/.stack-context.tmp"
render_file "$PROMPT_DIR/planner.md" "$CURRENT/planner.md" IssueText "$CURRENT/.issue.tmp" LocalCheck "$CURRENT/.local-check.tmp" StackContext "$CURRENT/.stack-context.tmp"; rm -f "$CURRENT"/.issue.tmp "$CURRENT"/.local-check.tmp "$CURRENT"/.stack-context.tmp
jq -n --arg status "Prepared" --argjson apiCommitMode true --arg createdAt "$(date -u --iso-8601=seconds)" --arg owner "$OWNER" --arg repo "$REPO" --arg repoFull "$FULL" --argjson issueNumber "$issue_number" --arg issueTitle "$issue_title" --arg issueUrl "$issue_url" --rawfile issueText "$CURRENT/issue.md" --argjson labels "$labels_json" --arg base "$BASE" --arg remote "$REMOTE" --arg branchName "$branch_name" --arg baseSha "$base_sha" --arg baseTreeSha "$base_tree_sha" --arg profilesCsv "$profiles_csv" --arg localCheck "$local_check" --arg stackContext "$stack_context" --arg promptDir "$PROMPT_DIR" --arg profilesPath "$PROFILES_PATH" --arg runDir "$(realpath "$CURRENT")" '{Status:$status,ApiCommitMode:$apiCommitMode,CreatedAt:$createdAt,Username:$owner,Repo:$repo,RepoFullName:$repoFull,IssueNumber:$issueNumber,IssueTitle:$issueTitle,IssueUrl:$issueUrl,IssueText:$issueText,Labels:$labels,Base:$base,Remote:$remote,BranchName:$branchName,BaseSha:$baseSha,BaseTreeSha:$baseTreeSha,LastCommitSha:"",ProfilesCsv:$profilesCsv,LocalCheck:$localCheck,StackContext:$stackContext,PromptDir:$promptDir,ProfilesPath:$profilesPath,RunDir:$runDir,PrUrl:"",PrNumber:0,LastLocalCheckPassed:false}' > "$CURRENT/state.json"
trap - ERR
echo "PREPARED"; echo "Issue: #$issue_number"; echo "Branch: $branch_name"; echo "Planner prompt: $CURRENT/planner.md"
