#!/usr/bin/env bash
set -euo pipefail
source "${AUTOMATION_ROOT:-~/automation}/scripts/lib.sh"
for cmd in codex git gh dotnet jq rg timeout ssh sha256sum base64 python3; do require_cmd "$cmd"; printf 'OK: %-12s %s\n' "$cmd" "$(command -v "$cmd")"; done
if command -v pwsh >/dev/null 2>&1; then echo "OK: pwsh $(command -v pwsh)"; else echo "NOTE: pwsh missing; install only if repo checks need it"; fi
mkdir -p ~/repos ~/automation/{scripts,logs,state,prompts,systemd}
echo "Setup check completed."
