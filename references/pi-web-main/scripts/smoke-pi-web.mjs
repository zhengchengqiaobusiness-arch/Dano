import { spawn } from "node:child_process";
import {
  existsSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { setTimeout as delay } from "node:timers/promises";
import { fileURLToPath } from "node:url";
import { WebSocket } from "ws";

const READY_TIMEOUT_MS = 30000;
const EXIT_TIMEOUT_MS = 10000;
const POLL_MS = 200;
const scriptDir = dirname(fileURLToPath(import.meta.url));
const projectRoot = join(scriptDir, "..");

function resolvePiCommand() {
  const localCli = join(
    projectRoot,
    "node_modules",
    "@earendil-works",
    "pi-coding-agent",
    "dist",
    "cli.js",
  );

  if (existsSync(localCli)) {
    return {
      command: process.execPath,
      args: [localCli],
    };
  }

  return {
    command: process.platform === "win32" ? "pi.cmd" : "pi",
    args: [],
  };
}

async function waitForReadyFile(readyFile, exitPromise) {
  const deadline = Date.now() + READY_TIMEOUT_MS;

  while (Date.now() < deadline) {
    if (existsSync(readyFile)) {
      return JSON.parse(readFileSync(readyFile, "utf8"));
    }

    const result = await Promise.race([
      exitPromise.then(exit => ({ type: "exit", exit })),
      delay(POLL_MS).then(() => ({ type: "tick" })),
    ]);

    if (result.type === "exit") {
      throw new Error(
        `pi exited before /web became ready (code=${result.exit.code}, signal=${result.exit.signal ?? "none"})`,
      );
    }
  }

  throw new Error(`Timed out waiting for ready file: ${readyFile}`);
}

async function waitForExit(exitPromise) {
  const result = await Promise.race([
    exitPromise,
    delay(EXIT_TIMEOUT_MS).then(() => {
      throw new Error(
        "Timed out waiting for pi to exit after shutdown request",
      );
    }),
  ]);

  if (result.code !== 0) {
    throw new Error(
      `pi exited with code=${result.code} signal=${result.signal ?? "none"}`,
    );
  }
}

async function verifyWebSocket(wsUrl) {
  await new Promise((resolve, reject) => {
    const ws = new WebSocket(wsUrl);
    const timeout = setTimeout(() => {
      ws.terminate();
      reject(new Error(`Timed out connecting to ${wsUrl}`));
    }, EXIT_TIMEOUT_MS);

    ws.once("open", () => {
      ws.close();
    });
    ws.once("close", () => {
      clearTimeout(timeout);
      resolve(undefined);
    });
    ws.once("error", err => {
      clearTimeout(timeout);
      reject(err);
    });
  });
}

async function main() {
  const tempDir = mkdtempSync(join(tmpdir(), "pi-web-smoke-"));
  const readyFile = join(tempDir, "ready.json");
  const shutdownFile = join(tempDir, "shutdown.flag");
  let child;
  let stdout = "";
  let stderr = "";

  try {
    const piCommand = resolvePiCommand();

    child = spawn(
      piCommand.command,
      [...piCommand.args, "--no-session", "-p", "/web --headless"],
      {
        env: {
          ...process.env,
          PI_WEB_HEADLESS: "1",
          PI_WEB_READY_FILE: readyFile,
          PI_WEB_SHUTDOWN_FILE: shutdownFile,
          PI_BRIDGE_PORT: "0",
        },
        stdio: ["ignore", "pipe", "pipe"],
      },
    );

    child.stdout.on("data", chunk => {
      stdout += chunk.toString();
    });
    child.stderr.on("data", chunk => {
      stderr += chunk.toString();
    });

    const exitPromise = new Promise((resolve, reject) => {
      child.once("error", reject);
      child.once("exit", (code, signal) => {
        resolve({ code, signal });
      });
    });

    const ready = await waitForReadyFile(readyFile, exitPromise);
    const bridgeUrl = ready.bridgeUrl;
    const wsUrl = ready.wsUrl;

    if (typeof bridgeUrl !== "string" || typeof wsUrl !== "string") {
      throw new Error(
        `Ready file is missing bridge URLs: ${JSON.stringify(ready)}`,
      );
    }

    const response = await fetch(bridgeUrl);
    if (!response.ok) {
      throw new Error(`GET ${bridgeUrl} failed with status ${response.status}`);
    }

    const contentType = response.headers.get("content-type") || "";
    if (!contentType.includes("text/html")) {
      throw new Error(
        `Expected HTML from ${bridgeUrl}, got ${contentType || "unknown"}`,
      );
    }

    await verifyWebSocket(wsUrl);

    writeFileSync(shutdownFile, "shutdown\n", "utf8");
    await waitForExit(exitPromise);

    console.log(`pi /web smoke test passed at ${bridgeUrl}`);
  } catch (error) {
    if (child && !child.killed) {
      child.kill();
    }

    const message = error instanceof Error ? error.message : String(error);
    const details = [
      message,
      stdout ? `stdout:\n${stdout}` : "",
      stderr ? `stderr:\n${stderr}` : "",
    ]
      .filter(Boolean)
      .join("\n\n");

    throw new Error(details);
  } finally {
    rmSync(tempDir, { recursive: true, force: true });
  }
}

await main();
