#!/usr/bin/env bash
set -euo pipefail
source "${AUTOMATION_ROOT:-~/automation}/scripts/lib.sh"
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
tool_root="${AUTODEV_ROOT:-$(cd -- "$script_dir/../.." && pwd)}"
planner_helper="${AUTODEV_PLANNER_HELPER:-$tool_root/automation/prepare_planner_prompt.py}"
OWNER="${GITHUB_OWNER:-}"; REPO="${GITHUB_REPO:-}"; BASE="${BASE_BRANCH:-main}"; REMOTE="${REMOTE_NAME:-origin}"; ISSUE="${ISSUE_NUMBER:-}"; DESCRIPTION="${ISSUE_DESCRIPTION:-}"; DESCRIPTION_FILE="${ISSUE_DESCRIPTION_FILE:-}"; PROFILES="${PROFILES:-}"; LOCAL_CHECK="${LOCAL_CHECK:-}"; STACK_CONTEXT="${STACK_CONTEXT:-}"; FORCE_CURRENT="${FORCE_CURRENT:-false}"; PROVIDER_PROFILE="${PROVIDER_PROFILE:-}"; READER_PROVIDER="${READER_PROVIDER:-command}"; READER_MODEL="${READER_MODEL:-}"; READER_COMMAND="${READER_COMMAND:-}"; CODER_PROVIDER="${CODER_PROVIDER:-command}"; CODER_MODEL="${CODER_MODEL:-}"; CODER_COMMAND="${CODER_COMMAND:-}"
while [[ $# -gt 0 ]]; do case "$1" in --owner) OWNER="$2"; shift 2;; --repo) REPO="$2"; shift 2;; --base) BASE="$2"; shift 2;; --remote) REMOTE="$2"; shift 2;; --issue) ISSUE="$2"; shift 2;; --description) DESCRIPTION="$2"; shift 2;; --description-file) DESCRIPTION_FILE="$2"; shift 2;; --profiles) PROFILES="$2"; shift 2;; --local-check) LOCAL_CHECK="$2"; shift 2;; --stack-context) STACK_CONTEXT="$2"; shift 2;; --provider-profile) PROVIDER_PROFILE="$2"; shift 2;; --force-current) FORCE_CURRENT=true; shift;; --reader-provider) READER_PROVIDER="$2"; shift 2;; --reader-model) READER_MODEL="$2"; shift 2;; --reader-command) READER_COMMAND="$2"; shift 2;; --coder-provider) CODER_PROVIDER="$2"; shift 2;; --coder-model) CODER_MODEL="$2"; shift 2;; --coder-command) CODER_COMMAND="$2"; shift 2;; *) echo "Unknown arg: $1" >&2; exit 2;; esac; done
require_cmd gh; require_cmd jq; require_cmd sha256sum; require_cmd python3; init_gh_env
FULL="$(repo_full_name "$OWNER" "$REPO")"; RUN_ROOT=".codex-run"; CURRENT="$RUN_ROOT/current"; mkdir -p "$RUN_ROOT"
if [[ -d "$CURRENT" ]]; then if [[ "$FORCE_CURRENT" == true ]]; then rm -rf "$CURRENT"; else mv "$CURRENT" "$RUN_ROOT/archive-$(date -u +%Y%m%d-%H%M%S)"; fi; fi; mkdir -p "$CURRENT"
if [[ -n "$DESCRIPTION_FILE" ]]; then DESCRIPTION="$(cat "$DESCRIPTION_FILE")"; fi
[[ -n "$ISSUE" && -n "$DESCRIPTION" ]] && { echo "Use either --issue or --description, not both" >&2; exit 2; }
if [[ -n "$DESCRIPTION" ]]; then
  issue_title="$(printf '%s\n' "$DESCRIPTION" | sed -n '/[^[:space:]]/{s/^[[:space:]#-]*//;s/[[:space:]]*$//;p;q}')"
  [[ -n "$issue_title" ]] || issue_title="Local AutoDev task"
  issue_json="$(jq -n --arg title "$issue_title" --arg body "$DESCRIPTION" '{number:0,title:$title,body:$body,url:"",labels:[]}')"
elif [[ -n "$ISSUE" ]]; then
  issue_json="$(gh issue view "$ISSUE" --repo "$FULL" --json number,title,body,url,labels)"
else
  next_number="$(gh issue list --repo "$FULL" --state open --label "autodev:ready" --json number,title,labels --limit 50 | jq -r '[.[] | select(([.labels[].name] | index("autodev:running") | not) and ([.labels[].name] | index("autodev:blocked") | not))][0].number // empty')"
  [[ -z "$next_number" ]] && { echo "NO_READY_ISSUE"; exit 0; }
  issue_json="$(gh issue view "$next_number" --repo "$FULL" --json number,title,body,url,labels)"
fi
issue_number="$(jq -r '.number' <<< "$issue_json")"; issue_title="$(jq -r '.title' <<< "$issue_json")"; issue_url="$(jq -r '.url' <<< "$issue_json")"; labels_json="$(jq '[.labels[].name]' <<< "$issue_json")"
echo "Selected issue #$issue_number: $issue_title"
cleanup_on_fail(){ code=$?; if [[ $code -ne 0 && -n "${issue_number:-}" && "$issue_number" != "0" ]]; then gh issue edit "$issue_number" --repo "$FULL" --remove-label "autodev:running" --add-label "autodev:blocked" >/dev/null 2>&1 || true; gh issue comment "$issue_number" --repo "$FULL" --body "AutoDev automation prepare step failed. Check automation logs." >/dev/null 2>&1 || true; fi; exit $code; }
trap cleanup_on_fail ERR
[[ "$issue_number" == "0" ]] || gh issue edit "$issue_number" --repo "$FULL" --add-label "autodev:running" >/dev/null
resolved="$(resolve_profiles_json "$labels_json" "$PROFILES" "$LOCAL_CHECK" "$STACK_CONTEXT")"; profiles_csv="$(jq -r '.profilesCsv' <<< "$resolved")"; local_check="$(jq -r '.localCheck' <<< "$resolved")"; stack_context="$(jq -r '.stackContext' <<< "$resolved")"
base_ref="$(gh api "repos/$FULL/git/ref/heads/$BASE")"; base_sha="$(jq -r '.object.sha' <<< "$base_ref")"; base_commit="$(gh api "repos/$FULL/git/commits/$base_sha")"; base_tree_sha="$(jq -r '.tree.sha' <<< "$base_commit")"
slug="$(safe_slug "issue-$issue_number-$issue_title")"; branch_name="autodev/$slug-$(date -u +%Y%m%d-%H%M%S)"; body="$(jq -r '.body // ""' <<< "$issue_json")"
if [[ "$issue_number" == "0" ]]; then
cat > "$CURRENT/issue.md" <<ISSUEEOF
# Local AutoDev Task: $issue_title

$body
ISSUEEOF
else
cat > "$CURRENT/issue.md" <<ISSUEEOF
# GitHub Issue #$issue_number: $issue_title

URL: $issue_url

$body
ISSUEEOF
fi
write_workspace_snapshot "$CURRENT/workspace-snapshot.json"
reader_args=(--repo . --current-dir "$CURRENT" --issue-file "$CURRENT/issue.md" --labels-json "$labels_json" --local-check "$local_check" --stack-context "$stack_context" --reader-provider "$READER_PROVIDER" --coder-provider "$CODER_PROVIDER")
[[ -n "$PROVIDER_PROFILE" ]] && reader_args+=(--provider-profile "$PROVIDER_PROFILE")
[[ -n "$READER_MODEL" ]] && reader_args+=(--reader-model "$READER_MODEL")
[[ -n "$READER_COMMAND" ]] && reader_args+=(--reader-command "$READER_COMMAND")
[[ -n "$CODER_MODEL" ]] && reader_args+=(--coder-model "$CODER_MODEL")
[[ -n "$CODER_COMMAND" ]] && reader_args+=(--coder-command "$CODER_COMMAND")
python3 "$planner_helper" "${reader_args[@]}"
jq -n --arg status "Prepared" --argjson apiCommitMode true --arg createdAt "$(date -u --iso-8601=seconds)" --arg owner "$OWNER" --arg repo "$REPO" --arg repoFull "$FULL" --argjson issueNumber "$issue_number" --arg issueTitle "$issue_title" --arg issueUrl "$issue_url" --rawfile issueText "$CURRENT/issue.md" --argjson labels "$labels_json" --arg base "$BASE" --arg remote "$REMOTE" --arg branchName "$branch_name" --arg baseSha "$base_sha" --arg baseTreeSha "$base_tree_sha" --arg profilesCsv "$profiles_csv" --arg localCheck "$local_check" --arg stackContext "$stack_context" --arg promptDir "$PROMPT_DIR" --arg profilesPath "$PROFILES_PATH" --arg providerProfile "$PROVIDER_PROFILE" --arg runDir "$(realpath "$CURRENT")" '{Status:$status,ApiCommitMode:$apiCommitMode,CreatedAt:$createdAt,Username:$owner,Repo:$repo,RepoFullName:$repoFull,IssueNumber:$issueNumber,IssueTitle:$issueTitle,IssueUrl:$issueUrl,IssueText:$issueText,Labels:$labels,Base:$base,Remote:$remote,BranchName:$branchName,BaseSha:$baseSha,BaseTreeSha:$baseTreeSha,LastCommitSha:"",ProfilesCsv:$profilesCsv,LocalCheck:$localCheck,StackContext:$stackContext,PromptDir:$promptDir,ProfilesPath:$profilesPath,ProviderProfile:$providerProfile,RunDir:$runDir,PrUrl:"",PrNumber:0,LastLocalCheckPassed:false}' > "$CURRENT/state.json"
trap - ERR
echo "PREPARED"; echo "Issue: #$issue_number"; echo "Branch: $branch_name"; echo "Planner prompt: $CURRENT/planner.md"