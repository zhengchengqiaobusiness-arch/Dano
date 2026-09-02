import test from "node:test";
import assert from "node:assert/strict";
import { execFile, spawn } from "node:child_process";
import { once } from "node:events";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);

test("start.bat stops leftover Studio processes on the port instead of reusing them", { skip: process.platform !== "win32" }, async () => {
  const listener = spawn(process.execPath, ["-e", `
    const server = require('http').createServer((q,s)=>s.end('ok'));
    server.listen(0, '127.0.0.1', () => process.stdout.write(String(server.address().port)));
  `], { stdio: ["ignore", "pipe", "ignore"], windowsHide: true });

  let portText = "";
  listener.stdout.on("data", chunk => { portText += String(chunk); });
  await once(listener, "spawn");
  for (let i = 0; i < 40 && !portText; i++) await new Promise(resolve => setTimeout(resolve, 50));
  const listeningPort = Number(portText);
  assert.ok(listeningPort > 0, "leftover listener did not publish a port");
  const closed = once(listener, "exit").then(() => true);

  try {
    const result = await execFileAsync("cmd.exe", ["/d", "/c", "start.bat"], {
      cwd: process.cwd(),
      env: {
        ...process.env,
        BSS_PORT: String(listeningPort),
        BSS_OPEN_UI: "false",
        BSS_NO_PAUSE: "true",
        BSS_SKIP_WEB: "true"
      },
      timeout: 15_000
    });
    assert.match(result.stdout, /Leftover Studio processes were stopped/i);
    assert.match(result.stdout, /Temporary files were cleared/i);
    assert.equal(await Promise.race([
      closed,
      new Promise<boolean>(resolve => setTimeout(() => resolve(false), 2000))
    ]), true, "leftover listener should be killed");
  } finally {
    if (listener.exitCode === null && listener.signalCode === null) listener.kill();
  }
});
