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
  assert.doesNotMatch(html, /manual-controls|id="manual-text"|data-manual-key/);
  assert.match(html, /id="browser-ime"/);
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
  assert.match(html, /id="send-prompt"[^>]*>发送</);
  assert.match(html, /id="abort-prompt"[^>]*>终止</);
  assert.match(html, /id="browser-ime"/);
  assert.match(app, /async function abortAgent\(\)[\s\S]*\/api\/agent\/abort/);
  assert.match(app, /elements\.abortPrompt\.addEventListener\("click"/);
  assert.match(app, /void submitPrompt\(elements\.prompt\.value\)/);
  assert.doesNotMatch(app, /working \? "■" : "↑"/);
  assert.doesNotMatch(app, /if \(state\.agentStreaming\) void abortAgent\(\)/);
  assert.doesNotMatch(app, /Pi 正在工作，请先终止当前任务/);
  assert.match(css, /\.composer-footer button\.abort\s*\{[^}]*background:\s*var\(--red\)/s);
  assert.match(css, /\.composer-actions/);
  assert.match(bridge, /思考过程、阶段状态、工具使用说明和最终回答均使用简体中文/);
  assert.match(bridge, /recentUserActions/);
  assert.match(bridge, /Never use generated #el-id-\*/);
});

test("recording workbench can clear conversation history in one click", async () => {
  const [html, css, app, server, bridge] = await Promise.all([
    readFile(path.join(root, "web", "index.html"), "utf8"),
    readFile(path.join(root, "web", "styles.css"), "utf8"),
    readFile(path.join(root, "web", "app.js"), "utf8"),
    readFile(path.join(root, "src", "web", "server.ts"), "utf8"),
    readFile(path.join(root, "src", "web", "pi-rpc.ts"), "utf8")
  ]);

  assert.match(html, /id="clear-session"[^>]*>清空历史</);
  assert.match(css, /\.session-toolbar\s*\{/);
  assert.match(app, /async function clearSessionHistory\(\)[\s\S]*\/api\/session\/clear/);
  assert.match(app, /elements\.clearSession\.addEventListener\("click"/);
  assert.match(app, /studio_shutdown/);
  assert.match(server, /pathname === "\/api\/session\/clear"/);
  assert.match(server, /await pi\.newSession\(\)/);
  assert.match(bridge, /async newSession\(\)[\s\S]*type: "new_session"/);
});

test("starting a recording keeps the workbench conversation", async () => {
  const [app, server] = await Promise.all([
    readFile(path.join(root, "web", "app.js"), "utf8"),
    readFile(path.join(root, "src", "web", "server.ts"), "utf8")
  ]);
  const browserStart = server.match(/async function browserStart[\s\S]*?\n\}/)?.[0] || "";
  const openBrowser = app.match(/async function openBrowser[\s\S]*?\n\}/)?.[0] || "";
  const clearRoute = server.match(/pathname === "\/api\/session\/clear"[\s\S]*?return;/)?.[0] || "";

  assert.match(clearRoute, /await resetWorkbench\(\)/);
  assert.doesNotMatch(browserStart, /resetWorkbench|transcript\.clear|pi\.abort|abortAgent/);
  assert.doesNotMatch(openBrowser, /resetWorkbench/);
  assert.doesNotMatch(openBrowser, /工作台已清空/);
  assert.match(app, /if \(event\.type === "session_reset"\) resetWorkbench\(\)/);
  assert.doesNotMatch(server, /studio\.recorder\.control\(\{\s*action:\s*"goto"/);
});

test("embedded preview stays clickable in Pi automatic click mode", async () => {
  const [app, server, css] = await Promise.all([
    readFile(path.join(root, "web", "app.js"), "utf8"),
    readFile(path.join(root, "src", "web", "server.ts"), "utf8"),
    readFile(path.join(root, "web", "styles.css"), "utf8")
  ]);
  const manualCommand = app.match(/async function manualCommand[\s\S]*?\n\}/)?.[0] || "";
  const previewHandlers = [
    app.match(/elements\.browserFrame\.addEventListener\("click"[\s\S]*?\n\}\);/)?.[0] || "",
    app.match(/elements\.browserViewport\.addEventListener\("wheel"[\s\S]*?\n\}, \{ passive: false \}\);/)?.[0] || "",
    app.match(/elements\.browserViewport\.addEventListener\("keydown"[\s\S]*?\n\}\);/)?.[0] || ""
  ].join("\n");
  const userControlRoute = server.match(/pathname === "\/api\/browser\/manual"[\s\S]*?return;/)?.[0] || "";
  const piControlRoute = server.match(/pathname === "\/internal\/browser\/control"[\s\S]*?return;/)?.[0] || "";

  assert.match(manualCommand, /if \(!state\.browserActive\) return;/);
  assert.doesNotMatch(manualCommand, /browserMode !== "manual"/);
  assert.match(app, /flushImeText|action: "text"/);
  assert.match(previewHandlers, /if \(!state\.browserActive\) return;/);
  assert.doesNotMatch(previewHandlers, /browserMode !== "manual"/);
  assert.doesNotMatch(userControlRoute, /请先切换到手动录制模式/);
  assert.match(piControlRoute, /当前是手动录制模式；Pi 只能读取页面/);
  assert.match(app, /classList\.toggle\("interactive", state\.browserActive\)/);
  assert.match(css, /\.browser-viewport\.interactive/);
  assert.match(app, /if \(state\.pollInFlight\) return;/);
  assert.match(app, /if \(!state\.browserActive \|\| state\.frameLoading \|\| document\.hidden\) return;/);
  assert.match(app, /setInterval\(\(\) => \{ if \(!document\.hidden\) void pollBrowser\(\); \}, 1400\)/);
  assert.match(server, /Content-Type": "image\/jpeg"/);
  assert.doesNotMatch(app, /setInterval\(pollBrowser, 900\)/);
});

test("workbench operations execute without a confirmation dialog", async () => {
  const [extension, bridge, server, app, browserSkill] = await Promise.all([
    readFile(path.join(root, ".pi", "extensions", "business-skill-studio.ts"), "utf8"),
    readFile(path.join(root, "src", "web", "pi-rpc.ts"), "utf8"),
    readFile(path.join(root, "src", "web", "server.ts"), "utf8"),
    readFile(path.join(root, "web", "app.js"), "utf8"),
    readFile(path.join(root, ".pi", "skills", "control-in-app-browser", "SKILL.md"), "utf8")
  ]);
  const browserControl = extension.match(/name: "business_browser_control"[\s\S]*?^\s{2}\}\);/m)?.[0] || "";

  assert.doesNotMatch(browserControl, /ctx\.ui\.confirm|Confirm real browser write/);
  assert.match(extension, /studio\.execute\(cap\.id, executionInput, cap\.confirmation\.required\)/);
  assert.doesNotMatch(extension, /Write operation cancelled by user|用户取消了 Skill 导出|Binding approval cancelled/);
  assert.match(bridge, /Execute browser actions and business operations immediately/);
  assert.doesNotMatch(bridge, /must wait for the existing explicit confirmation dialog/);
  assert.match(server, /event\.method === "confirm"[\s\S]*respondToUi\(\{ id: event\.id, confirmed: true \}\)/);
  assert.match(app, /if \(request\.method === "confirm"\) \{\s*state\.currentUiRequest = request;\s*void closeConfirmation\(true\);/s);
  assert.match(browserSkill, /Execute click, fill, select, choose, press, submit, and navigation immediately/);
  assert.match(browserSkill, /recentUserActions/);
  assert.match(browserSkill, /#el-id-\*/);
  assert.match(browserSkill, /YYYY-MM-DD/);
  assert.match(bridge, /never click the dim overlay|Never click text=2/);
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
