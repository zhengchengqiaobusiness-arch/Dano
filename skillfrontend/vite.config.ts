import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { shouldRouteToPiCheck } from "./src/api/piCheckRoute";

// 出包/目录/token 必须先匹配 /v1/skills、/v1/settings/token，不能落到笼统的 /v1 网关。
const gateway = process.env.DANO_GATEWAY || "http://localhost:8077";
const piCheck = process.env.DANO_PI_CHECK || "http://127.0.0.1:18081";

const longProxy = { timeout: 0, proxyTimeout: 0 };

export default defineConfig({
  plugins: [
    react(),
    {
      name: "disable-http-timeouts",
      configureServer(devServer) {
        const apply = () => {
          if (!devServer.httpServer) return;
          devServer.httpServer.requestTimeout = 0;
          devServer.httpServer.headersTimeout = 0;
          devServer.httpServer.timeout = 0;
        };
        apply();
        devServer.httpServer?.once("listening", apply);
      },
    },
  ],
  server: {
    port: 5173,
    proxy: {
      "/v1/skills": { target: piCheck, changeOrigin: true, ...longProxy },
      "/v1/settings/token": { target: piCheck, changeOrigin: true, ...longProxy },
      "/v1/pi-recordings": { target: piCheck, changeOrigin: true, ...longProxy },
      "/v1/recording-results": {
        target: gateway,
        changeOrigin: true,
        ...longProxy,
        router(req: { url?: string }) {
          const path = String(req.url || "").split("?")[0];
          if (/export-skill|\/draft$|rec_/.test(path)) return piCheck;
          return gateway;
        },
      },
      "/v1": {
        target: gateway,
        changeOrigin: true,
        ws: true,
        ...longProxy,
        router(req: { url?: string; method?: string }) {
          const path = String(req.url || "").split("?")[0];
          if (shouldRouteToPiCheck(path, req.method || "POST")) return piCheck;
          return gateway;
        },
      },
      "/tenants": { target: gateway, changeOrigin: true },
      "/auth": { target: gateway, changeOrigin: true },
      "/onboarding": { target: gateway, changeOrigin: true, ws: true },
      "/settings/runtime": { target: gateway, changeOrigin: true },
      "/export": { target: gateway, changeOrigin: true },
      "/lifecycle": { target: gateway, changeOrigin: true },
      "/assurance": { target: gateway, changeOrigin: true },
      "/assets": { target: gateway, changeOrigin: true },
      "/health": { target: gateway, changeOrigin: true },
    },
  },
});
