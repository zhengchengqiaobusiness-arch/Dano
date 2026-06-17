import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 开发时把网关路径前缀代理到后端(默认 :8077;bat 会按实际端口设 DANO_GATEWAY),前端用相对路径调用。
const target = process.env.DANO_GATEWAY || "http://localhost:8077";
const proxy = Object.fromEntries(
  ["/v1", "/tenants", "/onboarding", "/lifecycle", "/assurance", "/assets", "/health"].map(
    (p) => [p, { target, changeOrigin: true }],
  ),
);

export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy },
});
