#!/bin/sh
set -eu

runtime_root="${DANO_RUNTIME_DIR:-/opt/dano/runtime-data}"
if [ -n "${PI_CODING_AGENT_DIR:-}" ]; then
  agent_dir="$PI_CODING_AGENT_DIR"
  legacy_agent_dir=""
else
  agent_dir="$runtime_root/.pi/agent"
  legacy_agent_dir="$runtime_root/default-settings/.pi/agent"
fi
export PI_CODING_AGENT_DIR="$agent_dir"
runtime_defaults_dir="${DANO_RUNTIME_DEFAULTS_DIR:-/app/deploy/runtime-defaults}"
runtime_tmp_dir="$runtime_root/.dano/tmp"
export TMPDIR="$runtime_tmp_dir"
export HEIMDALL_PROTECT_CONFIG_OVERLAY="${HEIMDALL_PROTECT_CONFIG_OVERLAY:-0}"
npm_registry="${NPM_REGISTRY:-${NPM_CONFIG_REGISTRY:-${DANO_DEFAULT_NPM_REGISTRY:-https://mirrors.cloud.tencent.com/npm/}}}"

mkdir -p "$agent_dir" "$runtime_tmp_dir"

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

copy_legacy_if_missing() {
  file_name="$1"
  source_path="$legacy_agent_dir/$file_name"
  target_path="$agent_dir/$file_name"

  if [ -n "$legacy_agent_dir" ] && [ -f "$source_path" ] && [ ! -f "$target_path" ]; then
    cp "$source_path" "$target_path"
  fi
}

copy_legacy_if_missing "SYSTEM.md"
copy_legacy_if_missing "settings.json"
copy_legacy_if_missing "heimdall.json"

copy_default_if_missing "SYSTEM.md"
copy_default_if_missing "settings.json"
copy_default_if_missing "heimdall.json"

if [ "$#" -eq 0 ]; then
  set -- node ./dist/server/main.js
fi

exec "$@"
