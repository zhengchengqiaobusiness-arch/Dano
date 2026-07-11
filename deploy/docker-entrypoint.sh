#!/bin/sh
set -eu

runtime_root="${DANO_RUNTIME_DIR:-/opt/dano/runtime-data}"
agent_dir="${PI_CODING_AGENT_DIR:-$runtime_root/.pi/agent}"
export PI_CODING_AGENT_DIR="$agent_dir"
runtime_defaults_dir="${DANO_RUNTIME_DEFAULTS_DIR:-/app/deploy/runtime-defaults}"
npm_registry="${NPM_REGISTRY:-${NPM_CONFIG_REGISTRY:-${DANO_DEFAULT_NPM_REGISTRY:-https://mirrors.cloud.tencent.com/npm/}}}"

mkdir -p "$agent_dir"
mkdir -p "$agent_dir/bin"

if fd_path="$(command -v fd)" && rg_path="$(command -v rg)"; then
  ln -sf "$fd_path" "$agent_dir/bin/fd"
  ln -sf "$rg_path" "$agent_dir/bin/rg"
fi

if command -v npm >/dev/null 2>&1; then
  npm config set registry "$npm_registry" >/dev/null
fi

if command -v pnpm >/dev/null 2>&1; then
  pnpm config set registry "$npm_registry" >/dev/null
fi

copy_default_if_missing() {
  file_name="$1"
  source_path="$runtime_defaults_dir/$file_name"
  target_path="$agent_dir/$file_name"

  if [ ! -f "$source_path" ]; then
    echo "[dano-entrypoint] warning: missing runtime default: $source_path" >&2
    return 0
  fi

  if [ -f "$target_path" ]; then
    return 0
  fi

  cp "$source_path" "$target_path"
}

copy_default_if_missing "SYSTEM.md"
copy_default_if_missing "settings.json"
copy_default_if_missing "heimdall.json"

if [ "$#" -eq 0 ]; then
  set -- node ./dist/server/main.js
fi

exec "$@"
