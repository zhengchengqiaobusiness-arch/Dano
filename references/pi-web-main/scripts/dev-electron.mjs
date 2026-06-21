import { spawn } from "node:child_process";
import { setTimeout as delay } from "node:timers/promises";
import { fileURLToPath } from "node:url";

const isWindows = process.platform === "win32";

const rootDir = fileURLToPath(new URL("..", import.meta.url));
const rendererUrl =
  process.env.PI_WEB_ELECTRON_RENDERER_URL || "http://127.0.0.1:5173";
const bridgePort = process.env.PI_WEB_ELECTRON_BRIDGE_PORT || "8080";
const workspacePath = process.env.PI_WEB_ELECTRON_WORKSPACE || rootDir;

let viteProcess;
let electronProcess;
let shuttingDown = false;

function spawnCommand(command, args, env = process.env) {
  return spawn(command, args, {
    cwd: rootDir,
    env,
    stdio: "inherit",
    shell: isWindows,
  });
}

async function terminateProcessTree(child, signal = "SIGTERM") {
  if (!child?.pid) {
    return;
  }

  if (isWindows) {
    await new Promise(resolve => {
      const killer = spawn(
        "taskkill",
        ["/pid", String(child.pid), "/t", "/f"],
        {
          stdio: "ignore",
          windowsHide: true,
        },
      );
      killer.on("exit", () => resolve());
      killer.on("error", () => resolve());
    });
    return;
  }

  child.kill(signal);
}

async function waitForUrl(url, timeoutMs = 30000) {
  const startedAt = Date.now();

  while (Date.now() - startedAt < timeoutMs) {
    try {
      const response = await fetch(url);
      if (response.ok) {
        return;
      }
    } catch {}

    await delay(500);
  }

  throw new Error(`Timed out waiting for ${url}`);
}

async function shutdown(code = 0) {
  if (shuttingDown) {
    return;
  }

  shuttingDown = true;
  await Promise.allSettled([
    terminateProcessTree(electronProcess),
    terminateProcessTree(viteProcess),
  ]);
  process.exit(code);
}

process.on("SIGINT", () => {
  void shutdown(130);
});
process.on("SIGTERM", () => {
  void shutdown(143);
});

viteProcess = spawnCommand("pnpm", [
  "-C",
  "packages/svelte",
  "dev",
  "--host",
  "127.0.0.1",
  "--strictPort",
]);

viteProcess.on("exit", code => {
  if (!shuttingDown) {
    void shutdown(code ?? 1);
  }
});

await waitForUrl(rendererUrl);

electronProcess = spawnCommand("pnpm", ["-C", "packages/electron", "dev"], {
  ...process.env,
  PI_WEB_ELECTRON_RENDERER_URL: rendererUrl,
  PI_WEB_ELECTRON_BRIDGE_PORT: bridgePort,
  PI_WEB_ELECTRON_WORKSPACE: workspacePath,
});

electronProcess.on("exit", code => {
  void shutdown(code ?? 0);
});
