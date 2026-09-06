ARG NODE_IMAGE=node:22-bookworm-slim
ARG RUNTIME_IMAGE=mcr.microsoft.com/playwright:v1.62.1-noble
FROM ${NODE_IMAGE} AS node-runtime
FROM ${RUNTIME_IMAGE}

ARG NPM_REGISTRY=https://registry.npmmirror.com

COPY --from=node-runtime /usr/local/ /usr/local/
RUN node --version

WORKDIR /studio

COPY package.json ./
RUN npm install --registry=${NPM_REGISTRY} --no-audit --no-fund

COPY . .

CMD ["npm", "start"]
