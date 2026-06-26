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
COPY packages/bridge/package.json packages/bridge/package.json
COPY packages/svelte/package.json packages/svelte/package.json
COPY patches patches
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
  && npm config set registry "$registry"
RUN apt-get update \
  && apt-get install -y --no-install-recommends ca-certificates \
  && sed -i 's|http://deb.debian.org/debian-security|http://mirrors.tencent.com/debian-security|g; s|http://deb.debian.org/debian|http://mirrors.tencent.com/debian|g' /etc/apt/sources.list.d/debian.sources \
  && apt-get update \
  && apt-get install -y --no-install-recommends bubblewrap curl python3 \
  && chmod u+s /usr/bin/bwrap \
  && rm -rf /var/lib/apt/lists/*
ENV NODE_ENV=production
ENV DANO_HOST=0.0.0.0
ENV DANO_PORT=8080
ENV DANO_DEFAULT_WORKSPACE_PATH=/tmp/dano

COPY --from=build /prod/dano/package.json ./package.json
COPY --from=build /app/package.json ./package-versions/package.json
COPY --from=build /prod/dano/node_modules ./node_modules
COPY --from=build /app/dist ./dist
COPY --from=build /app/web-dist ./web-dist
COPY --from=build /app/dano.config.json ./dano.config.json
COPY deploy/runtime-defaults ./deploy/runtime-defaults
COPY deploy/docker-entrypoint.sh ./deploy/docker-entrypoint.sh
RUN chmod +x ./deploy/docker-entrypoint.sh

EXPOSE 8080
ENTRYPOINT ["./deploy/docker-entrypoint.sh"]
CMD ["node", "./dist/bridge/standalone/main.js"]
