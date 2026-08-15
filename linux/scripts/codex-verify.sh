#!/usr/bin/env bash
set -euo pipefail
PROFILES="auto"
while [[ $# -gt 0 ]]; do case "$1" in --profiles) PROFILES="$2"; shift 2;; *) echo "Unknown arg: $1" >&2; exit 2;; esac; done
IFS=',; ' read -r -a RAW <<< "$PROFILES"; LIST=(); for p in "${RAW[@]}"; do [[ -n "$p" ]] && LIST+=("$(echo "$p"|tr '[:upper:]' '[:lower:]')"); done; [[ ${#LIST[@]} -eq 0 ]] && LIST=(auto)
has_dotnet(){ find . -type f \( -name '*.sln' -o -name '*.slnf' -o -name '*.csproj' \) -not -path '*/bin/*' -not -path '*/obj/*' | head -n1 | grep -q .; }
has_web(){ find . -type f -name package.json -not -path '*/node_modules/*' | head -n1 | grep -q .; }
has_python(){ find . -type f \( -name pyproject.toml -o -name requirements.txt -o -name pytest.ini \) -not -path '*/.venv/*' -not -path '*/venv/*' | head -n1 | grep -q .; }
has_maui(){ find . -type f -name '*.csproj' -not -path '*/bin/*' -not -path '*/obj/*' -print0 | xargs -0 -r grep -IlE '<UseMaui>[[:space:]]*true[[:space:]]*</UseMaui>' | head -n1 | grep -q .; }
preferred_slnf(){ find . -type f -name '*.slnf' -not -path '*/bin/*' -not -path '*/obj/*' | grep -Ei 'no-gui|nogui|headless|backend|server|api|ci|test' | head -n1 || true; }
preferred_sln(){ local n; n=$(find . -maxdepth 1 -type f -name '*.sln'|wc -l); if [[ $n -eq 1 ]]; then find . -maxdepth 1 -type f -name '*.sln'|head -n1; else find . -type f -name '*.sln' -not -path '*/bin/*' -not -path '*/obj/*'|head -n1; fi; }
run_dotnet(){ command -v dotnet >/dev/null || { echo dotnet not found >&2; exit 127; }; has_dotnet || { echo "No .NET project found"; return; }; slnf=$(preferred_slnf); if [[ -n "$slnf" ]]; then dotnet restore "$slnf"; dotnet build "$slnf" --no-restore; dotnet test "$slnf" --no-build; return; fi; if has_maui; then echo "MAUI detected; running non-MAUI test projects only"; mapfile -t tests < <(find . -type f -name '*.csproj' -not -path '*/bin/*' -not -path '*/obj/*' | while read -r p; do grep -qiE 'Microsoft\.NET\.Test\.Sdk|xunit|NUnit|MSTest' "$p" && ! grep -qiE 'Microsoft\.Maui|<UseMaui>\s*true\s*</UseMaui>' "$p" && echo "$p"; done); [[ ${#tests[@]} -eq 0 ]] && { echo "No non-MAUI tests found; skipping backend verification"; return; }; for t in "${tests[@]}"; do dotnet test "$t"; done; return; fi; sln=$(preferred_sln); if [[ -n "$sln" ]]; then dotnet restore "$sln"; dotnet build "$sln" --no-restore; dotnet test "$sln" --no-build; else dotnet restore; dotnet build --no-restore; dotnet test --no-build; fi; }
run_maui(){ command -v dotnet >/dev/null || { echo dotnet not found >&2; exit 127; }; mapfile -t apps < <(find . -type f -name '*.csproj' -not -path '*/bin/*' -not -path '*/obj/*' | while read -r p; do grep -qiE '<UseMaui>[[:space:]]*true[[:space:]]*</UseMaui>' "$p" && ! grep -qiE '<IsTestProject>[[:space:]]*true[[:space:]]*</IsTestProject>|Microsoft\.NET\.Test\.Sdk' "$p" && echo "$p"; done); [[ ${#apps[@]} -eq 0 ]] && { echo "No MAUI application projects found"; return; }; mapfile -t windows_tests < <(find . -type f -name '*.csproj' -not -path '*/bin/*' -not -path '*/obj/*' | while read -r p; do grep -qiE '<IsTestProject>[[:space:]]*true[[:space:]]*</IsTestProject>|Microsoft\.NET\.Test\.Sdk' "$p" && grep -qiE 'net[0-9]+(\.[0-9]+)?-windows' "$p" && echo "$p"; done); for t in "${windows_tests[@]}"; do echo "DEFERRED: Windows-targeted test project $t cannot run on Linux; verify it on Windows."; done; for p in "${apps[@]}"; do tfm=$(grep -oEi 'net[0-9]+(\.[0-9]+)?-android([0-9.]+)?' "$p" | head -n1 || true); if [[ -z "$tfm" ]]; then echo "DEFERRED: MAUI project $p has no Android target runnable on Linux; verify its platform targets on a compatible host."; continue; fi; echo "MAUI Linux verification: building $p for $tfm"; dotnet build "$p" -f "$tfm"; done; }
run_web(){ command -v npm >/dev/null || { echo npm not found >&2; exit 127; }; mapfile -t dirs < <(find . -type f -name package.json -not -path '*/node_modules/*' -printf '%h\n'|sort -u); [[ ${#dirs[@]} -eq 0 ]] && { echo "No package.json found"; return; }; for d in "${dirs[@]}"; do (cd "$d"; [[ -f package-lock.json ]] && npm ci || npm install; if jq -e '.scripts.lint' package.json >/dev/null 2>&1; then npm run lint; fi; if jq -e '.scripts.test' package.json >/dev/null 2>&1; then npm test; fi; if jq -e '.scripts.build' package.json >/dev/null 2>&1; then npm run build; fi); done; }
run_python(){ has_python || { echo "No Python project found"; return; }; if command -v pytest >/dev/null; then pytest; else python3 -m pytest; fi; }

for p in "${LIST[@]}"; do
  case "$p" in
    auto)
      if has_dotnet; then run_dotnet; fi
      if has_web; then run_web; fi
      if has_maui; then run_maui; fi
      if has_python; then run_python; fi
      ;;
    backend) run_dotnet ;;
    web) run_web ;;
    maui) run_maui ;;
    python) run_python ;;
    *)
      echo "Unsupported profile: $p" >&2
      exit 2
      ;;
  esac
done
