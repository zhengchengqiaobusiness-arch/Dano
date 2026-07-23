import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { createServer as createNetServer } from "node:net";
import { setTimeout as delay } from "node:timers/promises";

const chromeCandidates = [
  process.env.DANO_CHROME_EXECUTABLE,
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/Applications/Chromium.app/Contents/MacOS/Chromium",
  "/usr/bin/google-chrome",
  "/usr/bin/chromium",
  "/usr/bin/chromium-browser",
].filter(Boolean);

export function findChromeExecutable() {
  return chromeCandidates.find(candidate => existsSync(candidate));
}

export async function availablePort() {
  const server = createNetServer();
  await new Promise((resolveListen, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolveListen);
  });
  const address = server.address();
  assert.ok(address && typeof address === "object");
  await new Promise((resolveClose, reject) =>
    server.close(error => error ? reject(error) : resolveClose()),
  );
  return address.port;
}

export function startService(command, args, options = {}) {
  const service = spawn(command, args, {
    cwd: options.cwd,
    detached: true,
    env: options.env ?? process.env,
    stdio: ["ignore", "pipe", "pipe"],
  });
  for (const stream of [service.stdout, service.stderr]) {
    stream.setEncoding("utf8");
    stream.on("data", chunk => options.output?.push(chunk));
  }
  return service;
}

export async function waitForHttp(url, options = {}) {
  const deadline = Date.now() + (options.timeoutMs ?? 30_000);
  const isReady = options.isReady ?? (response => response.ok);
  let lastError;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(url);
      if (isReady(response)) return;
      lastError = new Error(`HTTP ${response.status}`);
    } catch (error) {
      lastError = error;
    }
    for (const service of options.services ?? []) {
      if (service && (service.exitCode !== null || service.signalCode !== null)) {
        throw new Error(
          `Service exited with ${service.exitCode ?? service.signalCode}`,
        );
      }
    }
    await delay(100);
  }
  throw new Error(
    `Service did not become ready: ${lastError?.message ?? "timeout"}`,
  );
}

export async function stopService(service) {
  if (!service || service.exitCode !== null || service.signalCode !== null) {
    return;
  }
  const exited = new Promise(resolveExit => service.once("exit", resolveExit));
  try {
    process.kill(-service.pid, "SIGTERM");
  } catch (error) {
    if (error?.code !== "ESRCH") throw error;
  }
  if (await Promise.race([exited.then(() => true), delay(3_000, false)])) return;
  try {
    process.kill(-service.pid, "SIGKILL");
  } catch (error) {
    if (error?.code !== "ESRCH") throw error;
  }
  await exited;
}
