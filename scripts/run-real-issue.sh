#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Run the real GitHub issue automation flow.

Examples:
  scripts/run-real-issue.sh --repo . --github-repo owner/AutoDev --issue 18 --mode plan-only --out .codex-run/real-issue-18
  scripts/run-real-issue.sh --repo . --github-repo owner/AutoDev --issue 18 --mode implement --out .codex-run/real-issue-18 --allow-dirty
  scripts/run-real-issue.sh --repo . --github-repo owner/AutoDev --issue 18 --mode pr --out .codex-run/real-issue-18
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
