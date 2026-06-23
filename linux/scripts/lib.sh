#!/usr/bin/env bash
set -euo pipefail
AUTOMATION_ROOT="${AUTOMATION_ROOT:-~/automation}"
PROMPT_DIR="${PROMPT_DIR:-$AUTOMATION_ROOT/prompts}"
PROFILES_PATH="${PROFILES_PATH:-$AUTOMATION_ROOT/codex-profiles.json}"
require_cmd(){ command -v "$1" >/dev/null 2>&1 || { echo "ERROR: missing command: $1" >&2; exit 127; }; }
init_gh_env(){ mkdir -p "${GH_CONFIG_DIR:-$AUTOMATION_ROOT/state/gh-config}"; export GH_CONFIG_DIR="${GH_CONFIG_DIR:-$AUTOMATION_ROOT/state/gh-config}"; export GH_PROMPT_DISABLED=1; }
repo_full_name(){ [[ -n "$1" && -n "$2" ]] || { echo "owner/repo required" >&2; exit 2; }; echo "$1/$2"; }
safe_slug(){ echo "$1" | tr '[:upper:]' '[:lower:]' | sed -E 's/[^a-z0-9._-]+/-/g; s/^-+//; s/-+$//; s/-{2,}/-/g' | cut -c1-120; }
is_ignored_path(){ local p="${1#./}"; case "/$p/" in */.git/*|*/.codex-run/*|*/bin/*|*/obj/*|*/node_modules/*|*/dist/*|*/build/*|*/coverage/*|*/.vs/*|*/.idea/*|*/.vscode/*|*/.venv/*|*/venv/*|*/__pycache__/*) return 0;; esac; [[ "$p" == "memory.md" || "$p" == */memory.md ]]; }
write_workspace_snapshot(){ local out="$1" tmp; tmp="$(mktemp)"; find . -type f -print0 | while IFS= read -r -d '' f; do rel="${f#./}"; is_ignored_path "$rel" && continue; hash="$(sha256sum "$f"|awk '{print $1}')"; jq -n --arg path "$rel" --arg hash "$hash" '{($path):$hash}'; done > "$tmp"; jq -s 'add // {}' "$tmp" > "$out"; rm -f "$tmp"; }
current_workspace_snapshot_json(){ local tmp; tmp="$(mktemp)"; write_workspace_snapshot "$tmp"; cat "$tmp"; rm -f "$tmp"; }
resolve_profiles_json(){ local labels_json="$1" explicit_profiles="${2:-}" explicit_local_check="${3:-}" explicit_stack_context="${4:-}"; jq -n --slurpfile cfg "$PROFILES_PATH" --argjson labels "$labels_json" --arg explicitProfiles "$explicit_profiles" --arg explicitLocalCheck "$explicit_local_check" --arg explicitStackContext "$explicit_stack_context" --arg automationRoot "$AUTOMATION_ROOT" '
 def split_profiles($s): if ($s|length)==0 then [] else ($s|ascii_downcase|split(",")|map(split(";"))|flatten|map(split(" "))|flatten|map(select(length>0))|unique) end;
 ($cfg[0]) as $c | (split_profiles($explicitProfiles)) as $explicit |
 (if ($explicit|length)>0 then $explicit else [$c.profiles|to_entries[]|select((.value.labels//[]) as $pl | any($pl[]; . as $l | ($labels|index($l))))|.key]|unique end) as $fromLabels |
 (if ($fromLabels|length)==0 then [($c.defaultProfile//"auto")] else $fromLabels end) as $selected0 |
 (if (($selected0|index("auto")) and ($selected0|length>1)) then ($selected0|map(select(.!="auto"))) else $selected0 end) as $selected |
 ([ $selected[] as $p | if $p=="auto" then "auto" else ($c.profiles[$p].verifyProfile//$p) end ]|unique) as $verify |
 ($verify|join(",")) as $profilesCsv |
 (if ($explicitLocalCheck|length)>0 then $explicitLocalCheck else (($c.verifyCommandTemplate//"bash \"{{AutomationRoot}}/scripts/codex-verify.sh\" --profiles \"{{ProfilesCsv}}\"")|gsub("\\{\\{AutomationRoot\\}\\}";$automationRoot)|gsub("\\{\\{ProfilesCsv\\}\\}";$profilesCsv)) end) as $localCheck |
 (if ($explicitStackContext|length)>0 then $explicitStackContext else ([ $selected[] as $p | if $p=="auto" then "No specific area profile was selected. Use repository AGENTS.md, README, project files, solution/package files, and CI configuration as the source of truth. Prefer the smallest safe scope." else ($c.profiles[$p].stackContext//"") end ]|map(select(length>0))|join("\n")) end) as $stackContext |
 {profiles:$selected,profilesCsv:$profilesCsv,localCheck:$localCheck,stackContext:$stackContext}' ; }
render_file(){ local template="$1" output="$2"; shift 2; cp "$template" "$output"; while [[ $# -gt 0 ]]; do key="$1" value_file="$2"; shift 2; python3 - "$output" "$key" "$value_file" <<'PY'
import sys
from pathlib import Path
p=Path(sys.argv[1]); key=sys.argv[2]; v=Path(sys.argv[3]).read_text(encoding='utf-8')
s=p.read_text(encoding='utf-8').replace('{{'+key+'}}', v)
p.write_text(s, encoding='utf-8')
PY
done; }
