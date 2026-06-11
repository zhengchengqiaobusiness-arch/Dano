import type { ChatServerConfig } from "../types.js";

export const DEFAULT_STANDALONE_CONFIG: ChatServerConfig = {
  host: "127.0.0.1",
  port: 8080,
  cwd: process.cwd(),
  heartbeatMs: 15_000,
};
