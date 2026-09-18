import * as bootstrap from "@josephyoung/pi-openviking/bootstrap";
import { fileURLToPath } from "node:url";
import { assertWorkerProviderApi, serveWorkerBroker } from "./worker-broker.js";

export async function runWorkerBroker(): Promise<void> {
  if (process.platform !== "linux" || process.getuid?.() !== 0 || !process.send || !process.connected) {
    throw new Error("PRIVILEGED_WORKER_BROKER_REQUIRED");
  }
  // The published 0.1.0 bootstrap ignores the custom-provider option. Reject
  // it explicitly instead of accidentally enabling unguarded native tools.
  assertWorkerProviderApi(bootstrap);
  const profile = JSON.parse(process.argv[2] ?? "null");
  if (!profile || typeof profile !== "object" || Array.isArray(profile)) throw new Error("INVALID_WORKER_BROKER_PROFILE");
  // Only the installed Dano provider is selectable. The parent cannot select
  // arbitrary code through a message or workspace path.
  const toolProviderModule = fileURLToPath(new URL("./heimdall-worker-tools.js", import.meta.url));
  const { worker } = await bootstrap.bootstrapProtectedWorker({ ...profile, toolProviderModule });
  if (!process.connected) { worker.close(); return; }
  serveWorkerBroker(worker, {
    get connected() { return Boolean(process.connected); },
    on: process.on.bind(process), off: process.off.bind(process),
    send: (value, callback) => process.send!(value, callback),
    disconnect: () => process.disconnect?.(),
  }, profile);
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  runWorkerBroker().catch(() => {
    // The launcher receives a closed channel, never exception details or paths.
    process.exitCode = 1;
    process.disconnect?.();
  });
}
