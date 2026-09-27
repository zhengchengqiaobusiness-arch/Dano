#!/bin/sh
set -eu

test "$#" -gt 0
if [ -n "${DANO_APT_MIRROR:-}" ]; then
  # Before CA bootstrap, only a plain HTTP mirror can be reached reliably.
  # Keep the override to a host[:port] so it cannot alter the apt source stanza.
  printf '%s' "$DANO_APT_MIRROR" | grep -Eq '^http://[A-Za-z0-9.-]+(:[0-9]+)?$'
  mirror="$DANO_APT_MIRROR"
else
  apt-get update
  apt-get install -y --no-install-recommends ca-certificates
  mirror='https://mirrors.cloud.tencent.com'
fi

sed -i "s|https\?://deb.debian.org/debian-security|${mirror}/debian-security|g; s|https\?://deb.debian.org/debian|${mirror}/debian|g" \
  /etc/apt/sources.list.d/debian.sources
apt-get update
apt-get install -y --no-install-recommends "$@"
rm -rf /var/lib/apt/lists/*
