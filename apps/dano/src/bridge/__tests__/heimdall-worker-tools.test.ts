import { mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, expect, it, vi } from "vitest";
import { createWorkerTools } from "../heimdall-worker-tools.js";

const roots: string[] = [];
const providers: Awaited<ReturnType<typeof createWorkerTools>>[] = [];
afterEach(async () => {
  for (const provider of providers.splice(0)) provider.close();
  vi.unstubAllEnvs();
  await Promise.all(roots.splice(0).map(root => rm(root, { recursive: true, force: true })));
});

async function harness() {
  const root = await mkdtemp(join(tmpdir(), "dano-heimdall-worker-"));
  roots.push(root);
  const workspace = join(root, "workspace");
  await mkdir(workspace);
  vi.stubEnv("PI_CODING_AGENT_DIR", join(root, "agent"));
  return { root, workspace, async start() {
    const provider = await createWorkerTools({ workspace });
    providers.push(provider);
    return { provider, execute: (name: string, input: Record<string, unknown>) =>
      provider.execute(name, input, new AbortController().signal, () => {}) };
  } };
}

it("runs native workspace tools through the real Heimdall guards", async () => {
  const h = await harness();
  const { execute } = await h.start();
  await execute("write", { path: "note.txt", content: "hello worker" });
  await execute("edit", { path: "note.txt", edits: [{ oldText: "hello", newText: "protected" }] });
  expect(await execute("read", { path: "note.txt" })).toMatchObject({
    content: [{ type: "text", text: "protected worker" }],
  });
  await writeFile(join(h.workspace, ".env"), "SYNTHETIC_SECRET=fixture");
  await expect(execute("read", { path: ".env" })).rejects.toThrow("WORKER_TOOL_BLOCKED");
  await expect(execute("write", { path: ".pi/heimdall.json", content: "{}" }))
    .rejects.toThrow("WORKER_TOOL_BLOCKED");
}, 30_000);

it("does not execute workspace extensions and rejects unknown or closed operations", async () => {
  const h = await harness();
  await mkdir(join(h.workspace, ".pi/extensions"), { recursive: true });
  const marker = join(h.root, "extension-ran");
  await writeFile(join(h.workspace, ".pi/extensions/untrusted.ts"),
    `import { writeFileSync } from 'node:fs'; export default function () { writeFileSync(${JSON.stringify(marker)}, 'bad'); }`);
  const { execute, provider } = await h.start();
  await expect(readFile(marker)).rejects.toMatchObject({ code: "ENOENT" });
  await expect(execute("arbitrary_tool", {})).rejects.toThrow("INVALID_WORKER_TOOL");
  provider.close();
  await expect(execute("write", { path: "after-close.txt", content: "bad" }))
    .rejects.toThrow("WORKER_GUARDS_UNAVAILABLE");
  await expect(readFile(join(h.workspace, "after-close.txt"))).rejects.toMatchObject({ code: "ENOENT" });
}, 30_000);

it("applies real tool-result filtering and checks cancellation before touching files", async () => {
  const h = await harness();
  await mkdir(join(h.workspace, ".pi"));
  await writeFile(join(h.workspace, ".pi/heimdall.json"), "{}");
  await writeFile(join(h.workspace, ".pi/visible.txt"), "visible");
  const { execute, provider } = await h.start();
  const listing = await execute("ls", { path: ".pi" });
  expect(JSON.stringify(listing)).toContain("visible.txt");
  expect(JSON.stringify(listing)).not.toContain("heimdall.json");
  const controller = new AbortController();
  controller.abort(new Error("cancelled by caller"));
  await expect(provider.execute("write", { path: "cancelled.txt", content: "bad" },
    controller.signal, () => {})).rejects.toThrow("cancelled by caller");
  await expect(readFile(join(h.workspace, "cancelled.txt"))).rejects.toMatchObject({ code: "ENOENT" });
}, 30_000);

it.runIf(process.platform !== "linux")("never falls back to unsandboxed shell execution", async () => {
  const h = await harness();
  const { execute } = await h.start();
  for (const name of ["bash", "user_bash"]) {
    await expect(execute(name, { command: "touch unsandboxed-marker" })).rejects.toThrow();
  }
  await expect(readFile(join(h.workspace, "unsandboxed-marker"))).rejects.toMatchObject({ code: "ENOENT" });
}, 30_000);
