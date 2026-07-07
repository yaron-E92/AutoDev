#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 2 ]]; then
  echo "Usage: with-env.sh <env-file> <command> [args...]" >&2
  exit 2
fi

ENV_FILE="$1"
shift

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Environment file not found: $ENV_FILE" >&2
  exit 2
fi

# Export all variables from the repo env file.
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

AUTOMATION_ROOT="${AUTOMATION_ROOT:-~/automation}"

if [[ -n "${KEEPASS_DB:-}" ]]; then
  KEEPASS_CLI="${KEEPASS_CLI:-keepassxc-cli}"
  KEEPASS_KEY_FILE="${KEEPASS_KEY_FILE:-}"
  KEEPASS_ENTRY_PATH="${KEEPASS_ENTRY_PATH:-}"
  KEEPASS_NO_PASSWORD="${KEEPASS_NO_PASSWORD:-1}"

  if [[ -z "$KEEPASS_ENTRY_PATH" ]]; then
    echo "KEEPASS_ENTRY_PATH is required when KEEPASS_DB is set." >&2
    exit 2
  fi

  args=(show --show-protected --attributes Password)

  if [[ "$KEEPASS_NO_PASSWORD" == "1" || "$KEEPASS_NO_PASSWORD" == "true" ]]; then
    args+=(--no-password)
  fi

  if [[ -n "$KEEPASS_KEY_FILE" ]]; then
    args+=(--key-file "$KEEPASS_KEY_FILE")
  fi

  args+=("$KEEPASS_DB" "$KEEPASS_ENTRY_PATH")

  export GH_TOKEN
  GH_TOKEN="$("$KEEPASS_CLI" "${args[@]}" | tr -d '\r' | sed -e 's/[[:space:]]*$//')"

  if [[ -z "$GH_TOKEN" ]]; then
    echo "KeePassXC returned an empty GitHub token." >&2
    exit 1
  fi
fi

export GH_PROMPT_DISABLED=1
if [[ -z "${GH_CONFIG_DIR:-}" && -z "${GH_TOKEN:-}" && -n "${SUDO_USER:-}" && "${SUDO_USER:-}" != "root" ]]; then
  sudo_home="$(getent passwd "$SUDO_USER" | cut -d: -f6 || true)"
  [[ -d "$sudo_home/.config/gh" ]] && GH_CONFIG_DIR="$sudo_home/.config/gh"
fi
if [[ -n "${GH_CONFIG_DIR:-}" ]]; then
  export GH_CONFIG_DIR
  mkdir -p "$GH_CONFIG_DIR"
  chmod 700 "$GH_CONFIG_DIR" || true
fi

if [[ -n "${REPO_PATH:-}" ]]; then
  cd "$REPO_PATH"
fi

exec "$@"
