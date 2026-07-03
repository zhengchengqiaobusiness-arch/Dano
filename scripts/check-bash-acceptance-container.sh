#!/bin/sh
set -eu

container_bin="${DANO_CONTAINER_BIN:-podman}"
image="${DANO_BASH_ACCEPTANCE_NODE_IMAGE:-node:22-bookworm-slim}"
repo_dir="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
runtime_dir="${DANO_RUNTIME_DIR:-/opt/dano/runtime-data}"
container_runtime="/runtime"

map_runtime_path() {
  case "$1" in
    "$runtime_dir"/*) printf '%s\n' "$container_runtime/${1#"$runtime_dir"/}" ;;
    "$runtime_dir") printf '%s\n' "$container_runtime" ;;
    *) printf '%s\n' "$1" ;;
  esac
}

session_arg="${1:-}"
if [ -n "$session_arg" ]; then
  shift
  session_arg="$(map_runtime_path "$session_arg")"
fi

set -- run --rm \
  -v "$repo_dir:/app:ro" \
  -v "$runtime_dir:$container_runtime:ro" \
  -w /app \
  -e DANO_RUNTIME_DIR="$container_runtime"

for name in \
  DANO_BASH_ACCEPTANCE_TEXT \
  DANO_BASH_ACCEPTANCE_SESSION \
  DANO_BASH_ACCEPTANCE_SINCE \
  DANO_BASH_ACCEPTANCE_MARKER \
  DANO_BASH_ACCEPTANCE_SCAN_ALL \
  DANO_BASH_ACCEPTANCE_REQUIRED_MARKERS \
  DANO_BASH_ACCEPTANCE_FORBIDDEN_MARKERS
do
  value="$(printenv "$name" 2>/dev/null || true)"
  if [ -n "$value" ]; then
    if [ "$name" = "DANO_BASH_ACCEPTANCE_SESSION" ]; then
      value="$(map_runtime_path "$value")"
    fi
    set -- "$@" -e "$name=$value"
  fi
done

set -- "$@" "$image" node /app/scripts/check-bash-acceptance.mjs
if [ -n "$session_arg" ]; then
  set -- "$@" "$session_arg"
fi
exec "$container_bin" "$@"
