/**
 * 对目标页开一场真实 PI 录制：开场、等人/PI 操作、停录。
 * 只许开一场/停录，不许喂 actions.json。
 */

const BASE = process.env.PI_CHECK_URL || "http://127.0.0.1:18080";

async function readJson(response) {
  const text = await response.text();
  try {
    return JSON.parse(text);
  } catch {
    throw new Error(text.slice(0, 300) || `HTTP ${response.status}`);
  }
}

async function api(method, pathname, body) {
  const response = await fetch(`${BASE}${pathname}`, {
    method,
    headers: body ? { "content-type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  const payload = await readJson(response);
  if (!response.ok) {
    throw new Error(payload.error || payload.detail || `HTTP ${response.status}`);
  }
  return payload;
}

async function waitReady(id, timeoutMs = 90000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const session = await api("GET", `/api/recordings/${id}`);
    if (session.browserStatus === "ready" || session.status === "recording") return session;
    if (session.status === "failed") throw new Error(session.publicMessage || session.error || "录制启动失败");
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }
  throw new Error("录制浏览器没有就绪");
}

const targetUrl = process.argv[2];
const goal = process.argv[3] || "请将我接下来在页面中实际完成的每项业务操作分别生成一个可调用能力。";
if (!targetUrl) {
  console.error("usage: node scripts/record-page-flow.mjs <url> [goal]");
  process.exit(1);
}

const started = await api("POST", "/api/recordings", { targetUrl, goal });
const id = started.id || started.session?.id;
if (!id) throw new Error("没有 recording id");
console.log(`started ${id}`);
await waitReady(id);
console.log("ready; operate in the preview, then this script will stop");
const stopped = await api("POST", `/api/recordings/${id}/stop`);
const caps = stopped.result?.capabilities || [];
console.log(`status=${stopped.session?.status || stopped.status} capabilities=${caps.length}`);
for (const cap of caps) {
  console.log(`- ${cap.capability_id} | ${cap.title || cap.name} | ${cap.kind}`);
}
const unresolved = stopped.result?.unresolved || [];
if (unresolved.length) {
  console.log(`unresolved=${JSON.stringify(unresolved)}`);
}
console.log(`RESULT_JSON ${JSON.stringify({ id, capabilityCount: caps.length, unresolved })}`);
