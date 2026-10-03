FROM node:22-bookworm

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm config set registry https://registry.npmmirror.com \
  && npm ci

RUN if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
      sed -i 's|http://deb.debian.org|http://mirrors.cloud.tencent.com|g; s|http://security.debian.org|http://mirrors.cloud.tencent.com|g' /etc/apt/sources.list.d/debian.sources; \
    fi \
 && if [ -f /etc/apt/sources.list ]; then \
      sed -i 's|http://deb.debian.org|http://mirrors.cloud.tencent.com|g; s|http://security.debian.org|http://mirrors.cloud.tencent.com|g' /etc/apt/sources.list; \
    fi

ENV PLAYWRIGHT_DOWNLOAD_HOST=https://npmmirror.com/mirrors/playwright
RUN npx playwright install --with-deps chromium

COPY . .

ENV HOST=0.0.0.0
ENV PORT=18081
EXPOSE 18081

CMD ["npm", "start"]
