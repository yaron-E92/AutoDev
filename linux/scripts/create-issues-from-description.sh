#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Create structured GitHub issues from rough descriptions.

Examples:
  linux/scripts/create-issues-from-description.sh --description "Add dry-run mode to AutoDev" --repo owner/AutoDev --model devstral-small2-12k
  linux/scripts/create-issues-from-description.sh --description-file ideas.md --repo-map repo-map.json --dry-run
  linux/scripts/create-issues-from-description.sh --description "Fix AutoDev docs" --repo owner/AutoDev --model devstral-small2-12k --create --yes
EOF
}

for required in python gh; do
  if ! command -v "$required" >/dev/null 2>&1; then
    echo "Missing required executable: $required" >&2
    exit 127
  fi
done

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd -- "$script_dir/../.." && pwd)"

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  usage
fi

cd "$repo_root"
exec python "$repo_root/automation/create_issues_from_description.py" "$@"
