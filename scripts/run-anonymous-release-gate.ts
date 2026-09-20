import { createHash, createHmac } from "node:crypto";
import * as fs from "node:fs";
import * as http from "node:http";
import * as path from "node:path";
import { createAnonymousUserContextResolver } from "../apps/dano/src/bridge/anonymous-user-context.ts";
import type { AuthHttpHandler } from "../apps/dano/src/bridge/server.ts";
import { createJwtUserContextResolver } from "../apps/dano/src/bridge/user-context.ts";
import { DEFAULT_BRIDGE_CONFIG } from "../apps/dano/src/bridge/types.ts";
import { startDanoServer } from "../apps/dano/src/server.ts";
import { auditRun } from "./check-anonymous-release-gate.mjs";

const rawRunRoot = process.argv[2] ?? process.env.DANO_ANONYMOUS_GATE_RUN;
if (typeof rawRunRoot !== "string" || rawRunRoot.trim() === "") throw new Error("run directory is required");
const runRoot = path.resolve(rawRunRoot);
const evidencePath = path.join(runRoot, "evidence.json");
const ledgerPath = path.join(runRoot, "ledger.ndjson");
const runtimeRoot = path.join(runRoot, "runtime");
const gatePort = Number.parseInt(process.env.DANO_ANONYMOUS_GATE_PORT ?? "8080", 10);
if (!Number.isSafeInteger(gatePort) || gatePort < 1 || gatePort > 65_535) throw new Error("invalid gate port");
const evidence = readJson(evidencePath);

let acceptanceNow = Date.now();
let lastOccurredAt = Date.parse(evidence.preparedAt);
let livePassed = false;
const resources = new Map<string, Resource>();
const jwtSecret = createHash("sha256").update(`${evidence.runId}:authenticated-fixture`).digest("hex");
const authenticatedResolver = createJwtUserContextResolver({
  runtimeRootPath: runtimeRoot,
  secret: jwtSecret,
  now: () => acceptanceNow,
});
const anonymousUsers = createAnonymousUserContextResolver({
  runtimeRootPath: runtimeRoot,
  secureCookie: false,
  authenticatedResolver,
  now: () => acceptanceNow,
  activityWriteIntervalMs: 500,
});

let heldResponse: http.ServerResponse | undefined;
let heldRequestFingerprint: string | undefined;
let heldObserved!: () => void;
let heldObservedPromise = new Promise<void>(resolve => (heldObserved = resolve));
const provider = http.createServer(async (request, response) => {
  const chunks: Buffer[] = [];
  for await (const chunk of request) chunks.push(Buffer.from(chunk));
  const body = Buffer.concat(chunks).toString("utf8");
  if (body.includes(evidence.markers.turn)) {
    heldResponse = response;
    heldRequestFingerprint = sha256(`${evidence.runId}:${evidence.markers.turn}`);
    heldObserved();
    return;
  }
  completion(response, "Acceptance fixture response");
});
await listen(provider, 0);
const providerAddress = provider.address();
if (!providerAddress || typeof providerAddress === "string") throw new Error("provider did not start");
const agentDir = path.join(runRoot, "agent");
fs.mkdirSync(agentDir, { recursive: true });
fs.writeFileSync(path.join(agentDir, "models.json"), JSON.stringify({
  providers: {
    "anonymous-gate": {
      baseUrl: `http://127.0.0.1:${providerAddress.port}/v1`,
      api: "openai-completions",
      apiKey: "test-only",
      models: [{ id: "anonymous-gate", name: "Anonymous Gate", reasoning: false, input: ["text"], contextWindow: 16_000, maxTokens: 256, cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 } }],
    },
  },
}));
fs.writeFileSync(path.join(agentDir, "settings.json"), JSON.stringify({ defaultProvider: "anonymous-gate", defaultModel: "anonymous-gate", defaultThinkingLevel: "off" }));
process.env.PI_CODING_AGENT_DIR = agentDir;

let controller: Awaited<ReturnType<typeof startDanoServer>>;
const acceptanceHandler: AuthHttpHandler = {
  async handle(req, res, url, lifecycle) {
    if (!url.pathname.startsWith("/api/acceptance/anonymous")) return false;
    try {
      if (req.method === "GET" && url.pathname === "/api/acceptance/anonymous") {
        return render(res, slot(url.searchParams.get("slot")));
      }
      if (req.method !== "POST") throw new HttpFailure(405, "POST required");
      const body = await form(req);
      const action = url.pathname.slice("/api/acceptance/anonymous/".length);
      const selectedSlot = slot(body.slot);
      if (action === "authenticate") return authenticate(res, selectedSlot);
      const resolution = await resolveCurrent(req);
      if (!resolution) throw new HttpFailure(401, "current User is unavailable");
      if (action === "own") await captureOwn(req, selectedSlot, resolution);
      else if (action === "cross") await captureCross(req, selectedSlot, resolution);
      else if (action === "idle-sweep") await idleSweep(req, selectedSlot, resolution);
      else if (action === "turn-start") await startHeldTurn(req, selectedSlot, resolution);
      else if (action === "turn-protected") await protectHeldTurn(selectedSlot, resolution);
      else if (action === "turn-release") await releaseHeldTurn(selectedSlot, resolution);
      else if (action === "post-turn-sweep") await postTurnSweep(selectedSlot, resolution);
      else if (action === "authenticated-retained") await authenticatedRetained(req, selectedSlot, resolution);
      else throw new HttpFailure(404, "unknown action");
      return render(res, selectedSlot, `${action} 已记录`);
    } catch (error) {
      const status = error instanceof HttpFailure ? error.status : 500;
      const message = error instanceof Error ? error.message : "acceptance action failed";
      if (status === 500) console.error(error);
      return renderError(res, status, message);
    }
  },
};

controller = await startDanoServer(
  {
    ...DEFAULT_BRIDGE_CONFIG,
    host: "127.0.0.1",
    port: gatePort,
    portMax: gatePort,
    upload: { ...DEFAULT_BRIDGE_CONFIG.upload, uploadDir: path.join(runtimeRoot, "uploads") },
  },
  {
    captureSigint: false,
    sessionsRootPath: path.join(runtimeRoot, "sessions"),
    userContextResolver: anonymousUsers,
    anonymousUsers,
    anonymousUserCleanup: { idleTtlMs: 1_000, intervalMs: 50 },
    authHttpHandler: acceptanceHandler,
  },
);
console.log(`[anonymous-gate] ready ${controller.getBridgeUrl()}`);
console.log(`[anonymous-gate] A control /api/acceptance/anonymous?slot=a`);
console.log(`[anonymous-gate] B control /api/acceptance/anonymous?slot=b`);
const stop = async () => {
  heldResponse?.destroy();
  provider.closeAllConnections();
  await controller.stop();
  await new Promise<void>(resolve => provider.close(() => resolve()));
  process.exit(0);
};
process.on("SIGINT", () => void stop());
process.on("SIGTERM", () => void stop());

async function resolveCurrent(req: http.IncomingMessage) {
  const resolution = await anonymousUsers.resolveExisting!(req.headers);
  return resolution
    ? {
        context: resolution.userContext,
        status: resolution.authentication.status,
      }
    : null;
}

async function captureOwn(req: http.IncomingMessage, selectedSlot: Slot, resolution: Resolution) {
  const expectedStatus = selectedSlot === "authenticated" ? "authenticated" : "anonymous";
  if (resolution.status !== expectedStatus) throw new HttpFailure(409, `${selectedSlot} requires ${expectedStatus} User`);
  if (resources.has(selectedSlot)) throw new HttpFailure(409, `${selectedSlot} resources already captured`);
  const clientId = await currentClient(req);
  const marker = evidence.markers[selectedSlot];
  const upload = await createUpload(req, clientId, marker, selectedSlot);
  const prompt = await executeCommand(req, clientId, { id: `own-prompt-${selectedSlot}`, type: "prompt", message: marker });
  if (prompt.payload?.success !== true) throw new HttpFailure(409, "own transcript prompt failed");
  const state = await executeCommand(req, clientId, { id: `own-state-${selectedSlot}`, type: "get_state" });
  const sessionPathValue = state.payload?.data?.sessionFile ?? state.payload?.data?.sessionPath;
  if (typeof sessionPathValue !== "string") throw new HttpFailure(409, "own session path was not projected");
  await waitForFile(sessionPathValue, marker);
  const sessionPath = fs.realpathSync(sessionPathValue);
  const transcript = fs.readFileSync(sessionPath, "utf8");
  const preview = await request(req, upload.previewUrl);
  const previewBody = Buffer.from(await preview.arrayBuffer()).toString("utf8");
  if (preview.status !== 200 || previewBody !== marker) throw new HttpFailure(409, "own preview did not match marker");
  const resource: Resource = {
    slot: selectedSlot,
    userId: resolution.context.user.id,
    clientId,
    workspacePath: path.resolve(upload.workspacePath),
    uploadId: upload.id,
    uploadPath: upload.path,
    previewUrl: upload.previewUrl,
    sessionPath,
    marker,
  };
  resources.set(selectedSlot, resource);
  append({
    type: "own", slot: selectedSlot, authenticationStatus: resolution.status,
    markerSha256: sha256(marker), transportBindingFingerprint: transportBinding(req), ownerFingerprint: sha256(resource.userId), clientFingerprint: sha256(clientId),
    workspaceFingerprint: sha256(resource.workspacePath), uploadFingerprint: sha256(resource.uploadId),
    sessionFingerprint: sha256(resource.sessionPath), transcriptContentSha256: sha256(transcript),
    ownPreviewHttpStatus: preview.status, ownPreviewSha256: sha256(previewBody),
  });
}

async function captureCross(req: http.IncomingMessage, selectedSlot: Slot, resolution: Resolution) {
  if (selectedSlot !== "a" && selectedSlot !== "b") throw new HttpFailure(409, "cross probe requires a or b");
  const source = requiredResource(selectedSlot);
  const target = requiredResource(selectedSlot === "a" ? "b" : "a");
  if (sha256(resolution.context.user.id) !== sha256(source.userId)) throw new HttpFailure(403, "source User mismatch");
  const client = await request(req, `/api/clients/${encodeURIComponent(target.clientId)}/messages`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ type: "command", payload: { id: `cross-${selectedSlot}`, type: "get_state" } }) });
  const preference = await request(req, `/api/clients/${encodeURIComponent(target.clientId)}/preferences/theme`);
  const preview = await request(req, target.previewUrl);
  const session = await executeCommand(req, source.clientId, { id: `cross-session-${selectedSlot}`, type: "switch_session", sessionPath: target.sessionPath });
  const transcripts = await executeCommand(req, source.clientId, { id: `cross-transcripts-${selectedSlot}`, type: "list_sessions", workspacePath: source.workspacePath });
  const workspace = await executeCommand(req, source.clientId, { id: `cross-workspace-${selectedSlot}`, type: "list_workspace_entries", workspacePath: target.workspacePath, force: true });
  const file = await executeCommand(req, source.clientId, { id: `cross-file-${selectedSlot}`, type: "read_workspace_file", workspacePath: target.workspacePath, path: path.relative(target.workspacePath, target.uploadPath) });
  if ([client.status, preference.status, preview.status].some(value => value !== 403) || session.payload?.success !== false || transcripts.payload?.success !== true || JSON.stringify(transcripts.payload).includes(target.sessionPath) || workspace.payload?.success !== false || file.payload?.success !== false) throw new HttpFailure(409, "cross-User session/transcript/workspace/file probe was not rejected");
  append({ type: "cross", slot: selectedSlot, sourceClientFingerprint: sha256(source.clientId), targetClientFingerprint: sha256(target.clientId), targetUploadFingerprint: sha256(target.uploadId), targetSessionFingerprint: sha256(target.sessionPath), targetClientHttpStatus: client.status, targetPreferenceHttpStatus: preference.status, targetPreviewHttpStatus: preview.status, targetSessionResult: "rejected", targetTranscriptResult: "absent", targetWorkspaceResult: "rejected", targetFileResult: "rejected" });
}

async function idleSweep(req: http.IncomingMessage, selectedSlot: Slot, resolution: Resolution) {
  if (selectedSlot !== "b") throw new HttpFailure(409, "idle sweep is observed from b");
  const a = requiredResource("a"), b = requiredResource("b");
  if (resolution.context.user.id !== b.userId) throw new HttpFailure(403, "observer User mismatch");
  acceptanceNow += 2_000;
  // Cleanup includes asynchronous runtime disposal and filesystem work. Observe
  // completion instead of assuming it finishes within one short timer tick.
  const deadline = Date.now() + 3_000;
  while (!removed(a) && Date.now() < deadline) {
    if (!exists(b)) throw new HttpFailure(409, "active SSE protection did not occur");
    await delay(25);
  }
  if (!removed(a) || !exists(b)) throw new HttpFailure(409, "idle cleanup or active SSE protection did not occur");
  const command = await request(req, `/api/clients/${encodeURIComponent(b.clientId)}/messages`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ type: "command", payload: { id: "retained-b", type: "get_state" } }) });
  const preview = await request(req, b.previewUrl); const body = await preview.text();
  append({ type: "idle-sweep", removedOwnerFingerprint: sha256(a.userId), removedWorkspaceFingerprint: sha256(a.workspacePath), removedUploadFingerprint: sha256(a.uploadId), removedSessionFingerprint: sha256(a.sessionPath), retainedOwnerFingerprint: sha256(b.userId), retainedWorkspaceFingerprint: sha256(b.workspacePath), retainedUploadFingerprint: sha256(b.uploadId), retainedSessionFingerprint: sha256(b.sessionPath), retainedCommandHttpStatus: command.status, retainedPreviewHttpStatus: preview.status, retainedPreviewSha256: sha256(body) });
}

async function startHeldTurn(req: http.IncomingMessage, selectedSlot: Slot, resolution: Resolution) {
  if (selectedSlot !== "a2") throw new HttpFailure(409, "held Turn requires a2");
  const a2 = requiredResource("a2");
  if (resolution.context.user.id !== a2.userId) throw new HttpFailure(403, "a2 User mismatch");
  const prompt = await startPrompt(req, a2.clientId, evidence.markers.turn);
  await Promise.race([heldObservedPromise, delay(3_000).then(() => { throw new HttpFailure(409, "provider did not observe held Turn"); })]);
  const disconnected = await request(req, `/api/clients/${encodeURIComponent(a2.clientId)}/messages`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ type: "command", payload: { id: "no-sse-a2", type: "get_state" } }) });
  if (disconnected.status !== 409 || !heldRequestFingerprint) throw new HttpFailure(409, "a2 did not lose SSE while Turn remained active");
  append({ type: "turn-started", slot: "a2", ownerFingerprint: sha256(a2.userId), clientFingerprint: sha256(a2.clientId), workspaceFingerprint: sha256(a2.workspacePath), uploadFingerprint: sha256(a2.uploadId), sessionFingerprint: sha256(a2.sessionPath), turnMarkerSha256: sha256(evidence.markers.turn), promptHttpStatus: prompt, providerRequestFingerprint: heldRequestFingerprint });
}

async function protectHeldTurn(selectedSlot: Slot, resolution: Resolution) {
  if (selectedSlot !== "b") throw new HttpFailure(409, "Turn protection is observed from b");
  const a2 = requiredResource("a2"), b = requiredResource("b");
  if (resolution.context.user.id !== b.userId || !heldResponse || !heldRequestFingerprint) throw new HttpFailure(409, "held Turn is unavailable");
  acceptanceNow += 2_000; await delay(150);
  if (!exists(a2) || !exists(b)) throw new HttpFailure(409, "active Turn or SSE User was removed");
  const upload = loadUpload(a2.uploadId);
  append({ type: "turn-protected", ownerFingerprint: sha256(a2.userId), workspaceFingerprint: sha256(a2.workspacePath), uploadFingerprint: sha256(a2.uploadId), sessionFingerprint: sha256(a2.sessionPath), providerRequestFingerprint: heldRequestFingerprint, disconnectedClientHttpStatus: 409, retainedPreviewHttpStatus: 200, retainedPreviewSha256: sha256(fs.readFileSync(upload.path, "utf8")) });
}

async function releaseHeldTurn(selectedSlot: Slot, resolution: Resolution) {
  if (selectedSlot !== "b" || resolution.context.user.id !== requiredResource("b").userId || !heldResponse || !heldRequestFingerprint) throw new HttpFailure(409, "held Turn cannot be released");
  const fingerprint = heldRequestFingerprint;
  completion(heldResponse, "Held acceptance Turn released");
  heldResponse = undefined;
  await delay(150);
  append({ type: "turn-released", providerRequestFingerprint: fingerprint, providerResponseHttpStatus: 200 });
}

async function postTurnSweep(selectedSlot: Slot, resolution: Resolution) {
  if (selectedSlot !== "b") throw new HttpFailure(409, "post-Turn sweep is observed from b");
  const a2 = requiredResource("a2"), b = requiredResource("b");
  if (resolution.context.user.id !== b.userId) throw new HttpFailure(403, "observer mismatch");
  acceptanceNow += 2_000; await delay(150);
  if (!removed(a2) || !exists(b)) throw new HttpFailure(409, "completed Turn User was not cleaned");
  append({ type: "post-turn-sweep", removedOwnerFingerprint: sha256(a2.userId), removedWorkspaceFingerprint: sha256(a2.workspacePath), removedUploadFingerprint: sha256(a2.uploadId), removedSessionFingerprint: sha256(a2.sessionPath), retainedOwnerFingerprint: sha256(b.userId), retainedWorkspaceFingerprint: sha256(b.workspacePath), retainedUploadFingerprint: sha256(b.uploadId), retainedSessionFingerprint: sha256(b.sessionPath) });
}

async function authenticatedRetained(req: http.IncomingMessage, selectedSlot: Slot, resolution: Resolution) {
  if (selectedSlot !== "authenticated" || resolution.status !== "authenticated") throw new HttpFailure(409, "authenticated User required");
  const auth = requiredResource("authenticated");
  acceptanceNow += 2_000; await delay(150);
  if (!exists(auth)) throw new HttpFailure(409, "authenticated User was removed by Anonymous sweep");
  const command = await executeCommand(req, auth.clientId, { id: "auth-retained", type: "get_state" });
  if (command.payload?.success !== true) throw new HttpFailure(409, "authenticated command failed");
  const preview = await request(req, auth.previewUrl); const body = await preview.text();
  append({ type: "authenticated-retained", ownerFingerprint: sha256(auth.userId), workspaceFingerprint: sha256(auth.workspacePath), uploadFingerprint: sha256(auth.uploadId), sessionFingerprint: sha256(auth.sessionPath), commandHttpStatus: 202, previewHttpStatus: preview.status, previewSha256: sha256(body) });
  auditRun(runRoot, { quiet: true });
  livePassed = true;
  console.log("[anonymous-gate] PASS live HTTP/SSE/runtime Anonymous User release gate");
}

function authenticate(res: http.ServerResponse, selectedSlot: Slot) {
  if (selectedSlot !== "authenticated") throw new HttpFailure(409, "authenticated slot required");
  const token = jwt("anonymous-gate-authenticated", "Authenticated acceptance User");
  res.setHeader("Set-Cookie", `dano_auth=${token}; Path=/; HttpOnly; SameSite=Lax`);
  return render(res, selectedSlot, "已建立受控 authenticated User；请重新打开 Dano 后执行 own");
}

async function currentClient(req: http.IncomingMessage) {
  for (const client of [...controller.getClients()].reverse()) {
    if ((await request(req, `/api/clients/${encodeURIComponent(client.id)}/preferences/theme`)).status === 200) return client.id;
  }
  throw new HttpFailure(409, "请先在当前浏览器打开 Dano 页面");
}

async function createUpload(req: http.IncomingMessage, clientId: string, marker: string, selectedSlot: string) {
  const response = await request(req, `/api/uploads?clientId=${encodeURIComponent(clientId)}&name=${selectedSlot}-acceptance.txt&mimeType=text/plain&sha256=${sha256(marker)}`, { method: "POST", body: marker });
  if (response.status !== 201) throw new HttpFailure(409, `upload failed (${response.status})`);
  const upload = await response.json() as { id: string; previewUrl: string; path: string };
  const stored = loadUpload(upload.id);
  return { ...upload, workspacePath: stored.workspacePath as string };
}

async function startPrompt(req: http.IncomingMessage, clientId: string, marker: string) {
  const events = await fetch(`http://127.0.0.1:${gatePort}/api/clients/${encodeURIComponent(clientId)}/events`, { headers: req.headers.cookie ? { Cookie: req.headers.cookie } : {} });
  if (events.status !== 200 || !events.body) throw new HttpFailure(409, "SSE did not open");
  const response = await request(req, `/api/clients/${encodeURIComponent(clientId)}/messages`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ type: "command", payload: { id: "held-turn", type: "prompt", message: marker } }) });
  await events.body.cancel();
  if (response.status !== 202) throw new HttpFailure(409, "prompt was not accepted");
  return response.status;
}

async function executeCommand(req: http.IncomingMessage, clientId: string, payload: Record<string, unknown>): Promise<any> {
  const id = payload.id;
  if (typeof id !== "string") throw new HttpFailure(500, "acceptance command id is required");
  const abort = new AbortController();
  const events = await fetch(`http://127.0.0.1:${gatePort}/api/clients/${encodeURIComponent(clientId)}/events`, { headers: req.headers.cookie ? { Cookie: req.headers.cookie } : {}, signal: abort.signal });
  if (events.status !== 200 || !events.body) throw new HttpFailure(409, "SSE did not open");
  const result = waitForResponse(events.body, id);
  try {
    const posted = await request(req, `/api/clients/${encodeURIComponent(clientId)}/messages`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ type: "command", payload }) });
    if (posted.status !== 202) throw new HttpFailure(409, `command was not accepted (${posted.status})`);
    return await result;
  } finally {
    abort.abort();
  }
}

async function waitForResponse(stream: ReadableStream<Uint8Array>, id: string) {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  const deadline = Date.now() + 5_000;
  try {
    while (Date.now() < deadline) {
      const remaining = deadline - Date.now();
      const chunk = await Promise.race([reader.read(), delay(remaining).then(() => ({ done: true, value: undefined }))]);
      if (chunk.done) break;
      buffer += decoder.decode(chunk.value, { stream: true });
      let boundary = buffer.indexOf("\n\n");
      while (boundary !== -1) {
        const frame = buffer.slice(0, boundary); buffer = buffer.slice(boundary + 2);
        const data = frame.split(/\r?\n/).filter(line => line.startsWith("data: ")).map(line => line.slice(6)).join("\n");
        if (data) {
          const message = JSON.parse(data);
          if (message.type === "response" && message.payload?.id === id) return message;
        }
        boundary = buffer.indexOf("\n\n");
      }
    }
  } finally {
    await reader.cancel().catch(() => undefined);
  }
  throw new HttpFailure(409, `timed out waiting for ${id}`);
}

async function waitForFile(file: string, marker: string) {
  const deadline = Date.now() + 3_000;
  while (Date.now() < deadline) {
    if (fs.existsSync(file) && fs.readFileSync(file, "utf8").includes(marker)) return;
    await delay(25);
  }
  throw new HttpFailure(409, "session transcript was not persisted");
}

function exists(resource: Resource) {
  return fs.existsSync(resource.workspacePath) && fs.existsSync(resource.sessionPath) && fs.existsSync(resource.uploadPath) && Boolean(findAnonymousRecord(resource.userId) || resource.slot === "authenticated") && fs.existsSync(uploadRecordPath(resource.uploadId));
}

function removed(resource: Resource) {
  return !fs.existsSync(resource.workspacePath) && !fs.existsSync(resource.sessionPath) && !fs.existsSync(resource.uploadPath) && !findAnonymousRecord(resource.userId) && !fs.existsSync(uploadRecordPath(resource.uploadId));
}

function loadUpload(id: string) {
  const file = uploadRecordPath(id); if (!fs.existsSync(file)) throw new HttpFailure(409, "upload record missing");
  const stored = readJson(file); return stored.upload ?? stored;
}

function uploadRecordPath(id: string) {
  const direct = path.join(runtimeRoot, "uploads", "records", `${id}.json`);
  if (fs.existsSync(direct)) return direct;
  const found = files(path.join(runtimeRoot, "uploads", "records")).find(file => { const stored = readJson(file); return (stored.upload ?? stored).id === id; });
  return found ?? direct;
}

function findAnonymousRecord(userId: string) {
  return files(path.join(runtimeRoot, "anonymous-sessions")).find(file => readJson(file).userId === userId);
}

function requiredResource(selectedSlot: string) {
  const resource = resources.get(selectedSlot); if (!resource) throw new HttpFailure(409, `${selectedSlot} resources are not captured`); return resource;
}

async function request(req: http.IncomingMessage, route: string, init: RequestInit = {}) {
  const headers = new Headers(init.headers); if (req.headers.cookie) headers.set("Cookie", req.headers.cookie);
  return fetch(new URL(route, `http://127.0.0.1:${gatePort}`), { ...init, headers });
}

function append(payload: Record<string, unknown>) {
  const sequence = fs.readFileSync(ledgerPath, "utf8").split(/\r?\n/).filter(Boolean).length + 1;
  const record = { sequence, ...payload, runId: evidence.runId, occurredAt: occurredAt() };
  fs.appendFileSync(ledgerPath, `${JSON.stringify(record)}\n`, { mode: 0o600 });
}

function render(res: http.ServerResponse, selectedSlot: Slot, message = "") {
  const actions = selectedSlot === "a" ? [["own", "1 捕获 A"], ["cross", "3 A→B 隔离"], ["authenticate", "切换受控 authenticated User"]]
    : selectedSlot === "b" ? [["own", "2 捕获 B"], ["cross", "4 B→A 隔离"], ["idle-sweep", "5 回收 idle A / 保留 SSE B"], ["turn-protected", "8 验证 active Turn"], ["turn-release", "9 释放 Turn"], ["post-turn-sweep", "10 回收已完成 Turn"]]
    : selectedSlot === "a2" ? [["own", "6 捕获 A2"], ["turn-start", "7 启动并断开 held Turn"]]
    : [["authenticate", "建立 authenticated User"], ["own", "11 捕获 authenticated User"], ["authenticated-retained", "12 验证 sweep 不删除 authenticated User"]];
  res.writeHead(200, { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store" });
  res.end(`<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Anonymous Gate</title><style>body{font:16px system-ui;max-width:720px;margin:40px auto;padding:0 20px}form{margin:12px 0}button{padding:10px 18px;border:1px solid #ddd;border-radius:8px;background:#fff}.ok{background:#ecfdf5;padding:10px}</style><h1>Anonymous Gate · ${selectedSlot}</h1>${message ? `<p class="ok">${escape(message)}</p>` : ""}<p>只使用请求携带的真实匿名 Cookie 绑定和公开 Dano HTTP/SSE；服务端不接受结果字段。transport binding 只证明 A/B Cookie 绑定不同，不证明浏览器类型；IAB/Chrome surface provenance 必须由外部实际浏览器验收记录。</p>${actions.map(([action, label]) => `<form method="post" action="/api/acceptance/anonymous/${action}"><input type="hidden" name="slot" value="${selectedSlot}"><button>${label}</button></form>`).join("")}<p>流水 ${sequence()}/12</p>${livePassed ? "<h2>PASS：live HTTP/SSE/runtime 行为已复查；不包含浏览器类型 provenance</h2>" : ""}</html>`);
  return true;
}

function renderError(res: http.ServerResponse, status: number, message: string) { res.writeHead(status, { "Content-Type": "text/html; charset=utf-8" }); res.end(`<h1>验收步骤失败</h1><p>${escape(message)}</p><a href="javascript:history.back()">返回</a>`); return true; }
function completion(res: http.ServerResponse, text: string) { res.writeHead(200, { "Content-Type": "text/event-stream" }); res.write(`data: ${JSON.stringify({ id: "gate", object: "chat.completion.chunk", created: 1, model: "anonymous-gate", choices: [{ index: 0, delta: { content: text }, finish_reason: null }] })}\n\n`); res.write(`data: ${JSON.stringify({ id: "gate", object: "chat.completion.chunk", created: 1, model: "anonymous-gate", choices: [{ index: 0, delta: {}, finish_reason: "stop" }] })}\n\n`); res.end("data: [DONE]\n\n"); }
function jwt(userId: string, name: string) { const encode = (value: unknown) => Buffer.from(JSON.stringify(value)).toString("base64url"); const unsigned = `${encode({ alg: "HS256", typ: "JWT" })}.${encode({ sub: userId, name, exp: Math.floor(acceptanceNow / 1000) + 3600 })}`; return `${unsigned}.${createHmac("sha256", jwtSecret).update(unsigned).digest("base64url")}`; }
async function form(req: http.IncomingMessage) { const chunks: Buffer[] = []; for await (const chunk of req) chunks.push(Buffer.from(chunk)); return Object.fromEntries(new URLSearchParams(Buffer.concat(chunks).toString("utf8"))); }
function slot(value: unknown): Slot { if (!new Set(["a", "b", "a2", "authenticated"]).has(value as string)) throw new HttpFailure(400, "invalid slot"); return value as Slot; }
function occurredAt() { lastOccurredAt = Math.max(Date.now(), lastOccurredAt + 1); return new Date(lastOccurredAt).toISOString(); }
function sequence() { return fs.readFileSync(ledgerPath, "utf8").split(/\r?\n/).filter(Boolean).length; }
function files(root: string): string[] { if (!fs.existsSync(root)) return []; return fs.readdirSync(root, { withFileTypes: true }).flatMap(entry => entry.isDirectory() ? files(path.join(root, entry.name)) : entry.isFile() ? [path.join(root, entry.name)] : []); }
function readJson(file: string) { return JSON.parse(fs.readFileSync(file, "utf8")); }
function sha256(value: string) { return createHash("sha256").update(value).digest("hex"); }
function transportBinding(req: http.IncomingMessage) { if (!req.headers.cookie) throw new HttpFailure(409, "current Cookie binding is missing"); return sha256(req.headers.cookie); }
function escape(value: string) { return value.replace(/[&<>"']/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]!); }
function delay(ms: number) { return new Promise(resolve => setTimeout(resolve, ms)); }
function listen(server: http.Server, port: number) { return new Promise<void>((resolve, reject) => { server.once("error", reject); server.listen(port, "127.0.0.1", resolve); }); }

type Slot = "a" | "b" | "a2" | "authenticated";
type Resolution = NonNullable<Awaited<ReturnType<typeof resolveCurrent>>>;
interface Resource { slot: Slot; userId: string; clientId: string; workspacePath: string; uploadId: string; uploadPath: string; previewUrl: string; sessionPath: string; marker: string }
class HttpFailure extends Error { constructor(readonly status: number, message: string) { super(message); } }
