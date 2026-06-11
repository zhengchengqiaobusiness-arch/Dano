FROM node:22-alpine AS build

WORKDIR /app
RUN corepack enable

COPY package.json pnpm-workspace.yaml tsconfig.json vitest.config.ts ./
COPY packages/bridge/package.json packages/bridge/package.json
COPY packages/svelte/package.json packages/svelte/package.json
COPY pnpm-lock.yaml* ./
RUN pnpm install --frozen-lockfile=false

COPY . .
RUN pnpm run build
RUN CI=true pnpm prune --prod

FROM node:22-alpine AS runtime

WORKDIR /app
ENV NODE_ENV=production
ENV DANO_HOST=0.0.0.0
ENV DANO_PORT=8080

COPY --from=build /app/package.json ./package.json
COPY --from=build /app/pnpm-workspace.yaml ./pnpm-workspace.yaml
COPY --from=build /app/node_modules ./node_modules
COPY --from=build /app/dist ./dist
COPY --from=build /app/web-dist ./web-dist
COPY --from=build /app/.pi ./.pi

EXPOSE 8080
CMD ["node", "./dist/bridge/standalone/main.js", "--host", "0.0.0.0", "--port", "8080"]
