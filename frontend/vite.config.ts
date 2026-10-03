import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { recordingResultsProxyTarget, shouldRouteToPiCheck } from "./src/api/piCheckRoute";

// 出包/目录/token 必须先匹配 /v1/skills、/v1/settings/token，不能落到笼统的 /v1 网关。
const gateway = process.env.DANO_GATEWAY || "http://localhost:8077";
const piCheck = process.env.DANO_PI_CHECK || "http://127.0.0.1:19081";
const rawBase = process.env.VITE_BASE || "/";
const base = rawBase.endsWith("/") ? rawBase : `${rawBase}/`;
const routePrefix = base === "/" ? "" : base.slice(0, -1);

const longProxy = { timeout: 0, proxyTimeout: 0 };

function mount(path: string) {
  return `${routePrefix}${path}`;
}

function stripPrefix(path: string) {
  if (!routePrefix) return path;
  const queryAt = path.indexOf("?");
  const pathname = queryAt === -1 ? path : path.slice(0, queryAt);
  const search = queryAt === -1 ? "" : path.slice(queryAt);
  if (pathname === routePrefix) return `/${search}`;
  if (pathname.startsWith(`${routePrefix}/`)) return `${pathname.slice(routePrefix.length)}${search}`;
  return path;
}

function forwardedPath(req: { url?: string }) {
  return stripPrefix(String(req.url || "").split("?")[0]);
}

export default defineConfig({
  base,
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
    host: "127.0.0.1",
    port: 19082,
    strictPort: true,
    ...(process.env.DISABLE_HMR === "1" ? { hmr: false } : {}),
    proxy: {
      [mount("/v1/skills")]: { target: piCheck, changeOrigin: true, rewrite: stripPrefix, ...longProxy },
      [mount("/v1/settings/token")]: { target: piCheck, changeOrigin: true, rewrite: stripPrefix, ...longProxy },
      [mount("/v1/export/directory")]: { target: piCheck, changeOrigin: true, rewrite: stripPrefix },
      [mount("/export/directory")]: { target: piCheck, changeOrigin: true, rewrite: stripPrefix },
      [mount("/v1/pi-recordings")]: { target: piCheck, changeOrigin: true, rewrite: stripPrefix, ...longProxy },
      [mount("/v1/recording-results")]: {
        target: gateway,
        changeOrigin: true,
        rewrite: stripPrefix,
        ...longProxy,
        router(req: { url?: string; method?: string }) {
          const path = forwardedPath(req);
          if (recordingResultsProxyTarget(path, req.method || "GET") === "piCheck") return piCheck;
          return gateway;
        },
      },
      [mount("/v1")]: {
        target: gateway,
        changeOrigin: true,
        ws: true,
        rewrite: stripPrefix,
        ...longProxy,
        router(req: { url?: string; method?: string }) {
          const path = forwardedPath(req);
          if (shouldRouteToPiCheck(path, req.method || "POST")) return piCheck;
          return gateway;
        },
      },
      [mount("/tenants")]: { target: gateway, changeOrigin: true, rewrite: stripPrefix },
      [mount("/auth")]: { target: gateway, changeOrigin: true, rewrite: stripPrefix },
      // 只代理录制 WebSocket，不代理整个 /onboarding（/onboarding/page 是 SPA 前端路由）
      [mount("/onboarding/page/record")]: { target: piCheck, changeOrigin: true, ws: true, rewrite: stripPrefix },
      [mount("/settings/runtime")]: { target: gateway, changeOrigin: true, rewrite: stripPrefix },
      [mount("/export")]: { target: gateway, changeOrigin: true, rewrite: stripPrefix },
      [mount("/lifecycle")]: { target: gateway, changeOrigin: true, rewrite: stripPrefix },
      [mount("/assurance")]: { target: gateway, changeOrigin: true, rewrite: stripPrefix },
      [mount("/assets")]: { target: gateway, changeOrigin: true, rewrite: stripPrefix },
      [mount("/health")]: { target: gateway, changeOrigin: true, rewrite: stripPrefix },
    },
  },
});
