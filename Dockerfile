FROM node:22-alpine AS build

WORKDIR /app
ENV COREPACK_HOME=/tmp/corepack
RUN corepack enable && corepack prepare pnpm@9.15.9 --activate
ARG NPM_CONFIG_REGISTRY=https://registry.npmjs.org/

COPY package.json pnpm-workspace.yaml tsconfig.json vitest.config.ts ./
COPY packages/bridge/package.json packages/bridge/package.json
COPY packages/svelte/package.json packages/svelte/package.json
COPY pnpm-lock.yaml* ./
RUN npm_config_registry="$NPM_CONFIG_REGISTRY" \
  npm_config_fetch_timeout=600000 \
  pnpm install --frozen-lockfile=false

COPY . .
RUN pnpm run build
RUN CI=true pnpm prune --prod

FROM node:22-alpine AS runtime

WORKDIR /app
ENV NODE_ENV=production
ENV DANO_HOST=0.0.0.0
ENV DANO_PORT=8080
ENV DANO_DEFAULT_WORKSPACE_PATH=/tmp/dano

COPY --from=build /app/package.json ./package.json
COPY --from=build /app/pnpm-workspace.yaml ./pnpm-workspace.yaml
COPY --from=build /app/node_modules ./node_modules
COPY --from=build /app/dist ./dist
COPY --from=build /app/web-dist ./web-dist
COPY deploy/runtime-defaults ./deploy/runtime-defaults
COPY deploy/docker-entrypoint.sh ./deploy/docker-entrypoint.sh
RUN chmod +x ./deploy/docker-entrypoint.sh

EXPOSE 8080
ENTRYPOINT ["./deploy/docker-entrypoint.sh"]
CMD ["node", "./dist/bridge/standalone/main.js"]
