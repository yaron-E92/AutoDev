#!/usr/bin/env bash
set -euo pipefail

source "${AUTOMATION_ROOT:-~/automation}/scripts/lib.sh"

MODE=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --mode) MODE="$2"; shift 2 ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done

[[ -n "$MODE" ]] || { echo "Missing --mode" >&2; exit 2; }
require_cmd gh
require_cmd python3
init_gh_env

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
tool_root="${AUTODEV_ROOT:-$(cd -- "$script_dir/../.." && pwd)}"
old_pythonpath="${PYTHONPATH:-}"
if [[ -n "$old_pythonpath" ]]; then
  export PYTHONPATH="$tool_root:$old_pythonpath"
else
  export PYTHONPATH="$tool_root"
fi

exec python3 -m automation.workflow_stage_legacy \
  --mode "$MODE" \
  --repo "$(pwd)" \
  --autodev-root "$tool_root"
