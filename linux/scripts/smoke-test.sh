#!/usr/bin/env bash
set -euo pipefail
source "${AUTOMATION_ROOT:-~/automation}/scripts/lib.sh"
REPO_PATH="${1:-${REPO_PATH:-~/repos}}"; REPO_FULL="${2:-${GITHUB_OWNER:-}/${GITHUB_REPO:-}}"
for cmd in codex git gh dotnet jq rg ssh; do require_cmd "$cmd"; done
init_gh_env
echo "== Tools =="; codex --version; git --version; gh --version; dotnet --version; jq --version; rg --version
echo "== gh auth =="; gh api user --jq '.login'
if [[ "$REPO_FULL" == */* && "$REPO_FULL" != "/" ]]; then gh repo view "$REPO_FULL" --json nameWithOwner --jq '.nameWithOwner'; fi
echo "== ssh git@github.com =="; set +e; ssh -T git@github.com </dev/null; code=$?; set -e; echo "ssh exit code: $code (1 is often OK for GitHub)"
echo "== Codex exec smoke =="; mkdir -p "$REPO_PATH"; cd "$REPO_PATH"; timeout 5m codex exec "Run only: pwd && whoami && command -v git && command -v gh && command -v dotnet. Report the exact output." --skip-git-repo-check
echo "Smoke test completed."
