#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Run the AutoDev real GitHub issue automation flow.

Examples:
  scripts/run-real-issue.sh --repo . --github-repo owner/AutoDev --issue 18 --mode plan-only --out .autodev-runs/issue-18 --reader-command "ollama run qwen35-9b-32k" --coder-command "ollama run devstral-small2-12k"
  scripts/run-real-issue.sh --repo . --github-repo owner/AutoDev --next --manage-labels --mode implement --out .autodev-runs/next --provider-config autodev-providers.json
  scripts/run-real-issue.sh --repo . --github-repo owner/AutoDev --issue 18 --mode pr --out .autodev-runs/issue-18 --reader-provider chat-completions --reader-base-url http://localhost:1234/v1 --coder-command "my-coder"
EOF
}

python_cmd=""
if command -v python3 >/dev/null 2>&1; then
  python_cmd="python3"
elif command -v python >/dev/null 2>&1; then
  python_cmd="python"
else
  echo "Missing required executable: python3 or python" >&2
  exit 127
fi

if ! command -v gh >/dev/null 2>&1; then
  echo "Missing required executable: gh" >&2
  exit 127
fi

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd -- "$script_dir/.." && pwd)"

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  usage
fi

cd "$repo_root"
exec "$python_cmd" "$repo_root/automation/run_real_issue.py" "$@"
