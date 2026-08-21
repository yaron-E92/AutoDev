#!/usr/bin/env bash
set -euo pipefail
source "${AUTOMATION_ROOT:-linux}/scripts/lib.sh"
OWNER=""; REPO=""; INCLUDE=false
while [[ $# -gt 0 ]]; do case "$1" in --owner) OWNER="$2"; shift 2;; --repo) REPO="$2"; shift 2;; --include-area-labels) INCLUDE=true; shift;; *) echo "Unknown arg: $1" >&2; exit 2;; esac; done
FULL="$(repo_full_name "$OWNER" "$REPO")"; init_gh_env; require_cmd gh
label(){ local n="$1" c="$2" d="$3"; if gh label view "$n" --repo "$FULL" >/dev/null 2>&1; then gh label edit "$n" --repo "$FULL" --color "$c" --description "$d" >/dev/null; echo "Updated $n"; else gh label create "$n" --repo "$FULL" --color "$c" --description "$d" >/dev/null; echo "Created $n"; fi; }
label "autodev:managed" "1D76DB" "Human authorization for autonomous AutoDev work"
label "autodev:ready" "0E8A16" "Derived: managed and currently runnable by AutoDev"
label "autodev:blocked" "D93F0B" "Derived: managed but blocked by open issue dependencies"
label "autodev:attention" "FBCA04" "Human attention is required before autonomous AutoDev work"
label "autodev:running" "5319E7" "Active AutoDev claim/run for this issue"
# Legacy lifecycle labels remain supported for repositories that already use them.
label "autodev:failed" "D93F0B" "AutoDev automation failed"
label "autodev:done" "5319E7" "AutoDev completed this issue"
if [[ "$INCLUDE" == true ]]; then label "area:backend" "5319E7" "Backend/API issue"; label "area:web" "1D76DB" "Web frontend issue"; label "area:maui" "FBCA04" "MAUI client issue"; label "area:python" "2EA44F" "Python issue"; fi