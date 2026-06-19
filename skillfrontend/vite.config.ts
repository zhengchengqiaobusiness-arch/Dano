import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 开发时把网关路径前缀代理到后端(默认 :8077;bat 会按实际端口设 DANO_GATEWAY),前端用相对路径调用。
const target = process.env.DANO_GATEWAY || "http://localhost:8077";
const proxy = Object.fromEntries(
  // 注意:/settings 同时是前端 SPA 路由,只能代理精确的 API 子路径 /settings/runtime,否则整页加载会被打到后端
  ["/v1", "/tenants", "/onboarding", "/settings/runtime", "/export", "/lifecycle", "/assurance", "/assets", "/health"].map(
    (p) => [p, { target, changeOrigin: true }],
  ),
);

export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy },
});
