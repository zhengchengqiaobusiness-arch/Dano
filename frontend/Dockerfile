FROM node:22-alpine

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm config set registry https://registry.npmmirror.com \
  && npm ci

COPY . .

EXPOSE 19082

CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0", "--port", "19082"]
