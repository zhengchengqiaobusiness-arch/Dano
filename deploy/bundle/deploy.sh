#!/bin/sh
set -eu
umask 077
bundle=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd -P)
if [ "${1:-}" = "--production-transaction" ]; then
    shift
    exec python3 "$bundle/release.py" "$@"
fi
exec python3 "$bundle/bundle.py" "$bundle" "$@"
