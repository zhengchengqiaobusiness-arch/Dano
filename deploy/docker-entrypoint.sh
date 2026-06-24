#!/bin/sh
set -eu

workspace="${DANO_DEFAULT_WORKSPACE_PATH:-${DANO_DEFAULT_WORKSPACE:-/tmp/dano}}"
runtime_defaults_dir="${DANO_RUNTIME_DEFAULTS_DIR:-/app/deploy/runtime-defaults}"
runtime_settings_dir="$workspace/.pi"

mkdir -p "$runtime_settings_dir"

copy_default_if_missing() {
  file_name="$1"
  source_path="$runtime_defaults_dir/$file_name"
  target_path="$runtime_settings_dir/$file_name"

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
  set -- node ./dist/bridge/standalone/main.js
fi

exec "$@"
