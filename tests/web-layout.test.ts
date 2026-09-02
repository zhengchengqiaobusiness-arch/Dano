import test from "node:test";
import assert from "node:assert/strict";
import path from "node:path";
import { readFile } from "node:fs/promises";

const root = path.resolve(import.meta.dirname, "..");

test("recording workspace stays on one page with an internal session scroller", async () => {
  const [html, css, app] = await Promise.all([
    readFile(path.join(root, "web", "index.html"), "utf8"),
    readFile(path.join(root, "web", "styles.css"), "utf8"),
    readFile(path.join(root, "web", "app.js"), "utf8")
  ]);

  assert.doesNotMatch(html, /Pi 操作助手|只连接本页内置 Playwright 浏览器/);
  assert.doesNotMatch(html, /PLAYWRIGHT 内置会话|业务系统浏览器|traffic-lights/);
  assert.doesNotMatch(html, /class="panel-heading"|class="agent-heading"/);
  assert.match(html, />接入并开始录制</);
  assert.doesNotMatch(html, /manual-controls|页面输入|id="manual-text"|data-manual-key/);
  assert.match(html, /class="browser-toolbar"[\s\S]*class="address-form"[\s\S]*class="mode-switch"[\s\S]*class="recording-session-controls"[\s\S]*id="reload-browser"/);
  assert.match(css, /body\s*\{[^}]*height:\s*100dvh[^}]*overflow:\s*hidden|html, body\s*\{[^}]*height:\s*100dvh[^}]*overflow:\s*hidden/s);
  assert.match(css, /\.app-shell\s*\{[^}]*height:\s*100dvh[^}]*min-height:\s*0/s);
  assert.match(css, /\.recording-view\s*\{[^}]*overflow:\s*hidden/s);
  assert.match(css, /\.conversation\s*\{[^}]*overflow-y:\s*auto/s);
  assert.match(css, /\.browser-toolbar\s*\{[^}]*flex-wrap:\s*nowrap/s);
  assert.match(app, /details\.className\s*=\s*"thinking-block";\s*details\.open\s*=\s*true/);
  assert.doesNotMatch(app, /details\.open\s*=\s*!item\.complete/);
  assert.doesNotMatch(app, /if\s*\(node\.classList\.contains\("thinking-block"\)\)\s*\{?\s*node\.open\s*=\s*false/);
});

test("Pi send button becomes an immediate abort control and thinking is requested in Chinese", async () => {
  const [html, app, css, bridge] = await Promise.all([
    readFile(path.join(root, "web", "index.html"), "utf8"),
    readFile(path.join(root, "web", "app.js"), "utf8"),
    readFile(path.join(root, "web", "styles.css"), "utf8"),
    readFile(path.join(root, "src", "web", "pi-rpc.ts"), "utf8")
  ]);

  assert.match(html, /id="composer-hint"/);
  assert.match(app, /async function abortAgent\(\)[\s\S]*\/api\/agent\/abort/);
  assert.match(app, /working \? "■" : "↑"/);
  assert.match(app, /if \(state\.agentStreaming\) void abortAgent\(\)/);
  assert.match(css, /\.composer-footer button\.abort\s*\{[^}]*background:\s*var\(--red\)/s);
  assert.match(bridge, /思考过程、阶段状态、工具使用说明和最终回答均使用简体中文/);
});

test("starting a recording resets the previous workbench session", async () => {
  const [app, server] = await Promise.all([
    readFile(path.join(root, "web", "app.js"), "utf8"),
    readFile(path.join(root, "src", "web", "server.ts"), "utf8")
  ]);

  assert.match(app, /function resetWorkbench\(\)/);
  assert.match(app, /if \(event\.type === "session_reset"\) resetWorkbench\(\)/);
  assert.match(app, /resetWorkbench\(\);\s*await api\("\/api\/browser\/open"/);
  assert.match(server, /async function resetWorkbench/);
  assert.match(server, /transcript\.clear\(\)/);
  assert.match(server, /abortAgent: true/);
  assert.doesNotMatch(server, /studio\.recorder\.control\(\{\s*action:\s*"goto"/);
});

test("capability catalog UI and HTTP surface are gone", async () => {
  const [html, css, app, server, workflow] = await Promise.all([
    readFile(path.join(root, "web", "index.html"), "utf8"),
    readFile(path.join(root, "web", "styles.css"), "utf8"),
    readFile(path.join(root, "web", "app.js"), "utf8"),
    readFile(path.join(root, "src", "web", "server.ts"), "utf8"),
    readFile(path.join(root, "web", "recording-workflow.js"), "utf8")
  ]);

  assert.doesNotMatch(html, /能力目录|data-view-panel="catalog"|analyze-catalog|mapping-modal|capability-form/);
  assert.doesNotMatch(css, /\.catalog-layout|\.capability-card|\.mapping-form|\.boundary-grid/);
  assert.doesNotMatch(app, /loadCatalog|renderCatalog|\/api\/catalog|catalog_changed|setView\("catalog"\)/);
  assert.doesNotMatch(server, /\/api\/catalog|\/api\/bindings\/approve|\/api\/candidates\/configure|\/api\/capabilities\/|catalog_changed/);
  assert.doesNotMatch(workflow, /\/api\/catalog\/analyze/);
  assert.match(workflow, /\/api\/browser\/stop/);
});
