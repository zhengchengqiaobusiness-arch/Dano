import { readFileSync } from "node:fs";
import type { Plugin } from "vite";
import { readShortcutLinks } from "./src/bridge/dano-config.js";

/** This allowlist runs before Vite's module graph sees any product config. */
export function publicConfigPlugin(): Plugin {
  const id = "virtual:dano-public-config";
  return {
    name: "dano-public-config",
    resolveId(source) { if (source === id) return `\0${id}`; },
    load(source) {
      if (source !== `\0${id}`) return;
      const config = JSON.parse(readFileSync(new URL("../../dano.config.json", import.meta.url), "utf8"));
      return `export default ${JSON.stringify({ productName: config.productName, shortcutLinks: readShortcutLinks(config.shortcutLinks) ?? [] })};`;
    },
    // Never serve the raw configuration through Vite's dev filesystem route.
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const path = decodeURIComponent((req.url ?? "").split("?")[0]!);
        if (path.includes("dano.config.json")) { res.statusCode = 404; res.end(); return; }
        next();
      });
    },
  };
}
