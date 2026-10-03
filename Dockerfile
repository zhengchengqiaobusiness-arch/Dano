FROM node:22-bookworm

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm config set registry https://registry.npmmirror.com \
  && npm ci

ENV PLAYWRIGHT_DOWNLOAD_HOST=https://npmmirror.com/mirrors/playwright
RUN npx playwright install --with-deps chromium

COPY . .

ENV HOST=0.0.0.0
ENV PORT=18081
EXPOSE 18081

CMD ["npm", "start"]
