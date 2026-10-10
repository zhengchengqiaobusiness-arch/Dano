#!/usr/bin/env bash
# Read-only capability checks. Optional missing features are data, not failures.
export LC_ALL=C LANG=C PYTHONIOENCODING=utf-8
source_dir=${1:-/root/Dano-source}
available() { if command -v "$1" >/dev/null 2>&1; then printf true; else printf false; fi; }
supports() { if "$@" >/dev/null 2>&1; then printf true; else printf false; fi; }
help_contains() {
  local flag=$1
  shift
  if "$@" 2>/dev/null | grep -q -- "$flag"; then printf true; else printf false; fi
}
git_c=$(supports git -C "$source_dir" rev-parse --is-inside-work-tree)
git_show=false
git_symbolic=false
if test -d "$source_dir"; then
  git_show=$(cd "$source_dir" && supports git branch --show-current)
  git_symbolic=$(cd "$source_dir" && supports git symbolic-ref --short HEAD)
fi
printf '{"bash":%s,"sha256sum":%s,"flock":%s,"node":%s,"python3":%s,"gitC":%s,"gitShowCurrent":%s,"gitSymbolicRef":%s,"compose":%s,"composeInteractiveFlag":%s,"buildxFormat":%s}\n' \
  "$(available bash)" "$(available sha256sum)" "$(available flock)" \
  "$(available node)" "$(available python3)" "$git_c" "$git_show" "$git_symbolic" \
  "$(supports docker compose version)" \
  "$(help_contains --interactive docker compose run --help)" \
  "$(help_contains --format docker buildx du --help)"
