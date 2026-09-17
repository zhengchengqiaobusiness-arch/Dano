FROM node:22-bookworm-slim AS build

WORKDIR /app
ENV COREPACK_HOME=/tmp/corepack
ENV PNPM_HOME=/tmp/pnpm-home
ENV PNPM_STORE_DIR=/tmp/pnpm-store
ENV DANO_DEFAULT_NPM_REGISTRY=https://mirrors.cloud.tencent.com/npm/
ARG NPM_REGISTRY=
ARG NPM_CONFIG_REGISTRY=
RUN registry="${NPM_REGISTRY:-${NPM_CONFIG_REGISTRY:-$DANO_DEFAULT_NPM_REGISTRY}}" \
  && npm config set registry "$registry" \
  && npm_config_registry="$registry" corepack enable \
  && npm_config_registry="$registry" corepack prepare pnpm@9.15.9 --activate \
  && pnpm config set registry "$registry"

COPY package.json pnpm-workspace.yaml tsconfig.json vitest.config.ts ./
COPY apps/dano/package.json apps/dano/package.json
COPY pnpm-lock.yaml* ./
RUN registry="${NPM_REGISTRY:-${NPM_CONFIG_REGISTRY:-$DANO_DEFAULT_NPM_REGISTRY}}" \
  && npm_config_registry="$registry" \
  npm_config_fetch_timeout=600000 \
  pnpm install --frozen-lockfile=false --store-dir="$PNPM_STORE_DIR" --package-import-method=copy

COPY . .
RUN pnpm run build
RUN pnpm --filter @dano/app --prod deploy /prod/dano

FROM node:22-bookworm-slim AS runtime

WORKDIR /app
ENV DANO_DEFAULT_NPM_REGISTRY=https://mirrors.cloud.tencent.com/npm/
ARG NPM_REGISTRY=
ARG NPM_CONFIG_REGISTRY=
RUN registry="${NPM_REGISTRY:-${NPM_CONFIG_REGISTRY:-$DANO_DEFAULT_NPM_REGISTRY}}" \
  && npm config set registry "$registry" \
  && npm_config_registry="$registry" npm install --global open-websearch@2.1.11
RUN sed -i 's|https\?://deb.debian.org/debian-security|http://mirrors.aliyun.com/debian-security|g; s|https\?://deb.debian.org/debian|http://mirrors.aliyun.com/debian|g' /etc/apt/sources.list.d/debian.sources \
  && apt-get update \
  && apt-get install -y --no-install-recommends ca-certificates bubblewrap curl fd-find git python3 python3-venv ripgrep \
  && ln -sf "$(command -v fdfind)" /usr/local/bin/fd \
  && chmod 4755 /usr/bin/bwrap \
  && rm -rf /var/lib/apt/lists/*
COPY deploy/python-requirements.txt /app/deploy/python-requirements.txt
# /usr is readable in the tool sandbox; /opt is restricted to runtime skills.
RUN python3 -m venv /usr/local/lib/dano-python \
  && /usr/local/lib/dano-python/bin/pip install --no-cache-dir -r /app/deploy/python-requirements.txt \
  && /usr/local/lib/dano-python/bin/python -c 'import httpx'
ENV PATH="/usr/local/lib/dano-python/bin:${PATH}"
ENV NODE_ENV=production
ENV DANO_HOST=0.0.0.0
ENV DANO_PORT=8080
ENV DANO_RUNTIME_DIR=/opt/dano/runtime-data
ENV HOME=/home/node
ENV HEIMDALL_BWRAP_BIND_KERNEL_FS=1
ENV HEIMDALL_BWRAP_BIND_PROC=0
ENV HEIMDALL_BWRAP_BIND_ROOT=/opt/dano/runtime-data/workspaces

COPY --from=build /prod/dano/package.json ./package.json
COPY --from=build /app/package.json ./package-versions/package.json
COPY --from=build /prod/dano/node_modules ./node_modules
COPY --from=build /app/apps/dano/dist ./dist
COPY --from=build /app/dano.config.json ./dano.config.json
COPY deploy/runtime-defaults ./deploy/runtime-defaults
COPY deploy/activate-skill-seed.mjs ./deploy/activate-skill-seed.mjs
COPY deploy/docker-entrypoint.sh ./deploy/docker-entrypoint.sh
COPY deploy/render-system-prompt.mjs ./deploy/render-system-prompt.mjs
COPY deploy/system-prompt.mjs ./deploy/system-prompt.mjs
COPY apps/dano/runtime/skill-seed.mjs ./apps/dano/runtime/skill-seed.mjs
COPY apps/dano/runtime/system-prompt.mjs ./apps/dano/runtime/system-prompt.mjs
RUN mkdir -p /app/open-websearch-skill-seed \
  && cd /app/open-websearch-skill-seed \
  && registry="${NPM_REGISTRY:-${NPM_CONFIG_REGISTRY:-$DANO_DEFAULT_NPM_REGISTRY}}" \
  && npm_config_registry="$registry" \
  GIT_TERMINAL_PROMPT=0 \
  DISABLE_TELEMETRY=1 \
  npx --yes skills@1.5.9 add \
    https://github.com/Aas-ee/open-webSearch/tree/v2.1.11 \
    --skill open-websearch \
    --agent universal \
    --copy \
    --yes \
  && test -f .agents/skills/open-websearch/SKILL.md
RUN chmod +x ./deploy/docker-entrypoint.sh \
  && mkdir -p /opt/dano/runtime-data \
  && chown -R node:node /opt/dano /home/node

EXPOSE 8080
USER node
ENTRYPOINT ["./deploy/docker-entrypoint.sh"]
CMD ["node", "./dist/server/main.js"]
