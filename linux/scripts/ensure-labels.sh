#!/usr/bin/env bash
set -euo pipefail
source "${AUTOMATION_ROOT:-~/automation}/scripts/lib.sh"
OWNER=""; REPO=""; INCLUDE=false
while [[ $# -gt 0 ]]; do case "$1" in --owner) OWNER="$2"; shift 2;; --repo) REPO="$2"; shift 2;; --include-area-labels) INCLUDE=true; shift;; *) echo "Unknown arg: $1" >&2; exit 2;; esac; done
FULL="$(repo_full_name "$OWNER" "$REPO")"; init_gh_env; require_cmd gh
label(){ local n="$1" c="$2" d="$3"; if gh label view "$n" --repo "$FULL" >/dev/null 2>&1; then gh label edit "$n" --repo "$FULL" --color "$c" --description "$d" >/dev/null; echo "Updated $n"; else gh label create "$n" --repo "$FULL" --color "$c" --description "$d" >/dev/null; echo "Created $n"; fi; }
label "autodev:ready" "0E8A16" "Ready for AutoDev automation"
label "autodev:running" "FBCA04" "AutoDev automation is processing this issue"
label "autodev:blocked" "B60205" "AutoDev automation is blocked"
label "autodev:failed" "D93F0B" "AutoDev automation failed"
label "autodev:done" "5319E7" "AutoDev completed this issue"
if [[ "$INCLUDE" == true ]]; then label "area:backend" "5319E7" "Backend/API issue"; label "area:web" "1D76DB" "Web frontend issue"; label "area:maui" "FBCA04" "MAUI client issue"; label "area:python" "2EA44F" "Python issue"; fi
