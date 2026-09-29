#!/bin/sh
set -eu

# One public container entry. The supervisor drops privileges internally.
if [ "$#" -eq 0 ]; then
  set -- node ./dist/server/main.js
fi
if [ "$1" = "node" ] && [ "${2:-}" = "./dist/server/main.js" ]; then
  shift 2
  exec node ./dist/server/container-main.js "$@"
fi

runtime_root="${DANO_RUNTIME_DIR:-/opt/dano/runtime-data}"
agent_dir="${PI_CODING_AGENT_DIR:-$runtime_root/.pi/agent}"
export PI_CODING_AGENT_DIR="$agent_dir"
runtime_defaults_dir="${DANO_RUNTIME_DEFAULTS_DIR:-/app/deploy/runtime-defaults}"
skill_seed_dir="${DANO_SKILL_SEED_DIR:-/app/open-websearch-skill-seed/.agents/skills}"
agent_skills_dir="${DANO_SKILLS_DIR:-$agent_dir/skills}"
entrypoint_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
npm_registry="${NPM_REGISTRY:-${NPM_CONFIG_REGISTRY:-${DANO_DEFAULT_NPM_REGISTRY:-https://mirrors.cloud.tencent.com/npm/}}}"

mkdir -p "$agent_dir"

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

system_prompt_source="$runtime_defaults_dir/SYSTEM.md"
system_prompt_target="$agent_dir/SYSTEM.md"
if [ ! -f "$system_prompt_source" ]; then
  echo "[dano-entrypoint] warning: missing runtime default: $system_prompt_source" >&2
elif [ ! -f "$system_prompt_target" ]; then
  node "$entrypoint_dir/render-system-prompt.mjs" \
    --if-missing \
    "$system_prompt_source" \
    "$system_prompt_target"
fi

copy_default_if_missing "settings.json"
copy_default_if_missing "heimdall.json"
node "$entrypoint_dir/activate-skill-seed.mjs" \
  "$skill_seed_dir" \
  "$agent_skills_dir"

if [ "${1:-}" = "--initialize-only" ]; then
  exit 0
fi
exec "$@"
