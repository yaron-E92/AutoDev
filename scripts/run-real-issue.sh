#!/usr/bin/env bash
set -euo pipefail

script_path="${BASH_SOURCE[0]}"
while [[ -L "$script_path" ]]; do
  script_dir="$(cd -- "$(dirname -- "$script_path")" && pwd)"
  script_path="$(readlink -- "$script_path")"
  [[ "$script_path" != /* ]] && script_path="$script_dir/$script_path"
done
repo_root="$(cd -- "$(dirname -- "$script_path")/.." && pwd)"

exec "$repo_root/linux/scripts/issue-to-pr-cycle.sh" "$@"
