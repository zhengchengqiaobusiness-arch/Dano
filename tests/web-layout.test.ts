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
  assert.match(html, /id="stop-recording"[^>]*>结束录制</);
  assert.doesNotMatch(html, /browser-footer|id="browser-status"|id="browser-size"|1440 × 960/);
  assert.doesNotMatch(html, /等待会话|停止录制|id="finish-recording"|id="recording-state"|id="agent-status"|class="statusbar"|Business Skill Studio/);
  assert.match(html, /<strong>Skill Studio<\/strong>/);
  assert.doesNotMatch(css, /\.statusbar|\.recording-state/);
  assert.doesNotMatch(app, /finishRecording|recordingState|agentStatus\.innerHTML/);
  assert.match(css, /body\s*\{[^}]*height:\s*100dvh[^}]*overflow:\s*hidden|html, body\s*\{[^}]*height:\s*100dvh[^}]*overflow:\s*hidden/s);
  assert.match(css, /\.app-shell\s*\{[^}]*height:\s*100dvh[^}]*min-height:\s*0/s);
  assert.match(css, /\.recording-view\s*\{[^}]*overflow:\s*hidden/s);
  assert.match(css, /\.recording-view\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*2fr\) minmax\(240px,\s*\.48fr\)/s);
  assert.match(css, /\.conversation\s*\{[^}]*overflow-y:\s*auto/s);
  assert.match(css, /\.conversation\s*\{[^}]*overscroll-behavior:\s*contain/s);
  assert.doesNotMatch(app, /scrollHeight - scroller\.scrollTop - scroller\.clientHeight < 96/);
  assert.match(app, /sessionFollow/);
  assert.match(app, /conversation\.addEventListener\("scroll", syncSessionFollowFromUser/);
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
  const [html, css, app, server, bridge, page] = await Promise.all([
    readFile(path.join(root, "web", "index.html"), "utf8"),
    readFile(path.join(root, "web", "styles.css"), "utf8"),
    readFile(path.join(root, "web", "app.js"), "utf8"),
    readFile(path.join(root, "src", "web", "server.ts"), "utf8"),
    readFile(path.join(root, "src", "web", "pi-rpc.ts"), "utf8"),
    readFile(path.join(root, "src", "web", "workbench-page.ts"), "utf8")
  ]);
  const resetWorkbench = page.match(/async reset\(\)[\s\S]*?\n  \}/)?.[0] || "";

  assert.match(html, /id="clear-session"[^>]*>清空历史</);
  assert.match(css, /\.session-toolbar\s*\{/);
  assert.match(app, /async function clearSessionHistory\(\)[\s\S]*\/api\/session\/clear/);
  assert.match(app, /elements\.clearSession\.addEventListener\("click"/);
  assert.match(app, /resetBrowserWorkbench/);
  assert.match(app, /已结束录制并清空全部内容；下一条消息是新对话/);
  assert.match(app, /studio_shutdown/);
  assert.doesNotMatch(app, /window\.close\(/);
  assert.match(server, /pathname === "\/api\/session\/clear"/);
  assert.match(resetWorkbench, /this\.recorder\.disposeAndKill\("clear"\)/);
  assert.match(resetWorkbench, /abortWork\("clear"\)/);
  assert.match(resetWorkbench, /lastRecordingSessionId = undefined/);
  assert.match(resetWorkbench, /this\.pi\.stop\(\)/);
  assert.doesNotMatch(resetWorkbench, /beginFreshConversation/);
  assert.match(resetWorkbench, /ensureStarted\(\)/);
  assert.match(resetWorkbench, /this\.transcript\.clear\(\)/);
  assert.match(bridge, /this\.stopping = true/);
  assert.match(bridge, /async beginFreshConversation\(\)[\s\S]*type: "new_session"/);
  assert.match(bridge, /this\.suppressEvents = true/);
  assert.match(bridge, /async newSession\(\)[\s\S]*type: "new_session"/);
  assert.match(bridge, /清空历史 starts an independent conversation/);
  assert.match(app, /agent_error["'] && !state\.clearingSession/);
});

test("starting a recording keeps the workbench conversation", async () => {
  const [app, server, page] = await Promise.all([
    readFile(path.join(root, "web", "app.js"), "utf8"),
    readFile(path.join(root, "src", "web", "server.ts"), "utf8"),
    readFile(path.join(root, "src", "web", "workbench-page.ts"), "utf8")
  ]);
  const browserStart = page.match(/async startRecording[\s\S]*?\n  \}/)?.[0] || "";
  const openBrowser = app.match(/async function openBrowser[\s\S]*?\n\}/)?.[0] || "";
  const clearRoute = server.match(/pathname === "\/api\/session\/clear"[\s\S]*?return;/)?.[0] || "";

  assert.match(clearRoute, /await page\.reset\(\)/);
  assert.doesNotMatch(browserStart, /reset\(|transcript\.clear|pi\.abort|abortAgent|beginFreshConversation/);
  assert.doesNotMatch(openBrowser, /resetWorkbench/);
  assert.doesNotMatch(openBrowser, /工作台已清空/);
  assert.match(app, /event\.epoch <= state\.sessionEpoch/);
  assert.match(app, /function reconcileSession\(/);
  assert.match(app, /function adoptLocalUser\(/);
  assert.match(app, /local-user-/);
  assert.doesNotMatch(server, /studio\.recorder\.control\(\{\s*action:\s*"goto"/);
  const openRoute = server.match(/pathname === "\/api\/browser\/open"[\s\S]*?return;\s*\}/)?.[0] || "";
  const internalStart = server.match(/pathname === "\/internal\/browser\/start"[\s\S]*?return;\s*\}/)?.[0] || "";
  assert.doesNotMatch(openRoute, /evaluateRerecord/);
  assert.match(internalStart, /evaluateRerecord/);
  assert.match(internalStart, /!gate\.allowed && page\.lastRecordingSessionId/);
  assert.match(internalStart, /blocked: true/);
});

test("three failed form attempts expose a non-blocking manual takeover and resume endpoint", async () => {
  const [html, css, app, server] = await Promise.all([
    readFile(path.join(root, "web", "index.html"), "utf8"),
    readFile(path.join(root, "web", "styles.css"), "utf8"),
    readFile(path.join(root, "web", "app.js"), "utf8"),
    readFile(path.join(root, "src", "web", "server.ts"), "utf8")
  ]);
  assert.match(html, /id="manual-takeover"/);
  assert.match(html, /我已完成，继续自动执行/);
  assert.match(css, /\.manual-takeover\s*\{[^}]*position:\s*fixed/s);
  assert.doesNotMatch(html, /modal-backdrop[^>]*id="manual-takeover"/);
  assert.match(app, /manual_takeover_required/);
  assert.match(app, /\/api\/browser\/takeover\/complete/);
  assert.match(server, /await page\.requestManualTakeover/);
  assert.match(server, /if \(result\?\.stopped\)/);
  assert.doesNotMatch(server, /result\?\.followManualSteps\s*\|\|/);
  assert.match(server, /resumedAfterManualTakeover:\s*true/);
  assert.match(html, /自动操作已暂停/);
});

test("embedded preview stays clickable in Pi automatic click mode", async () => {
  const [app, server, css, recorder, page] = await Promise.all([
    readFile(path.join(root, "web", "app.js"), "utf8"),
    readFile(path.join(root, "src", "web", "server.ts"), "utf8"),
    readFile(path.join(root, "web", "styles.css"), "utf8"),
    readFile(path.join(root, "src", "browser", "recorder.ts"), "utf8"),
    readFile(path.join(root, "src", "web", "workbench-page.ts"), "utf8")
  ]);
  const manualCommand = app.match(/async function manualCommand[\s\S]*?\n\}/)?.[0] || "";
  const previewHandlers = [
    app.match(/elements\.browserFrame\.addEventListener\("pointerdown"[\s\S]*?\n\}\);/)?.[0] || "",
    app.match(/elements\.browserFrame\.addEventListener\("pointermove"[\s\S]*?\n\}\);/)?.[0] || "",
    app.match(/elements\.browserFrame\.addEventListener\("pointerup"[\s\S]*?\n\}\);/)?.[0] || "",
    app.match(/elements\.browserFrame\.addEventListener\("click"[\s\S]*?\n\}\);/)?.[0] || "",
    app.match(/elements\.browserViewport\.addEventListener\("wheel"[\s\S]*?\n\}, \{ passive: false \}\);/)?.[0] || "",
    app.match(/elements\.browserViewport\.addEventListener\("keydown"[\s\S]*?\n\}\);/)?.[0] || ""
  ].join("\n");
  const userControlRoute = server.match(/pathname === "\/api\/browser\/manual"[\s\S]*?return;/)?.[0] || "";
  const piControlRoute = server.match(/pathname === "\/internal\/browser\/control"[\s\S]*?return;/)?.[0] || "";

  assert.match(manualCommand, /if \(!state\.browserActive\) return;/);
  assert.doesNotMatch(manualCommand, /browserMode !== "manual"/);
  assert.match(app, /flushImeText|action: "text"/);
  assert.match(app, /action: "drag"/);
  assert.match(app, /phase: "start"/);
  assert.match(app, /phase: "move"/);
  assert.match(app, /enqueueDragCommand|pumpDragQueue/);
  assert.match(app, /lastFrameRect/);
  assert.match(app, /previewDrag/);
  assert.doesNotMatch(app, /points\.length >= 80/);
  assert.match(recorder, /action: "drag"/);
  assert.match(recorder, /phase === "start"|phase: "start"/);
  assert.match(recorder, /dragInterpolationSteps/);
  assert.match(css, /touch-action:\s*none/);
  assert.match(previewHandlers, /if \(!state\.browserActive\) return;/);
  assert.doesNotMatch(previewHandlers, /browserMode !== "manual"/);
  assert.doesNotMatch(userControlRoute, /请先切换到手动录制模式/);
  assert.match(piControlRoute, /当前是手动录制模式；Pi 只能读取页面/);
  assert.match(app, /classList\.toggle\("interactive", state\.browserActive\)/);
  assert.match(css, /\.browser-viewport\.interactive/);
  assert.doesNotMatch(css, /\.browser-viewport\.interactive\s*\{[^}]*outline/);
  assert.match(css, /\.browser-frame\s*\{[^}]*object-fit:\s*contain/);
  assert.doesNotMatch(css, /\.browser-frame\s*\{[^}]*object-fit:\s*fill/);
  assert.match(app, /function displayedFrameRect\(/);
  assert.match(app, /function previewPaneSize\(/);
  assert.doesNotMatch(app, /viewport: previewPaneSize\(\)/);
  assert.match(app, /function rememberPaneViewport\(/);
  assert.doesNotMatch(app, /\/api\/browser\/viewport/);
  assert.match(app, /ResizeObserver/);
  assert.match(server, /pathname === "\/api\/browser\/viewport"/);
  assert.match(recorder, /async fitViewport\(/);
  assert.match(page, /this\.recorder\.start\(url, name \|\| "web-session", this\.preferredViewport, expectedOperations, completeFieldCoverage \|\| this\.completeFieldCoverageRequested, completePageCoverage\)/);
  assert.match(css, /\.empty-browser\s*\{[^}]*max-width:\s*none/);
  assert.match(css, /\.browser-panel\s*\{[^}]*grid-template-rows:\s*40px minmax\(0,\s*1fr\)/);
  assert.doesNotMatch(css, /\.browser-footer/);
  assert.doesNotMatch(css, /\.browser-frame\s*\{[^}]*contain:\s*strict/);
  assert.doesNotMatch(await readFile(path.join(root, "src", "browser", "recorder.ts"), "utf8"), /startScreencast|screencastFrame|attachScreencast/);
  assert.match(app, /if \(state\.pollInFlight\) return;/);
  assert.match(app, /if \(!state\.browserActive \|\| state\.frameLoading \|\| document\.hidden\) return;/);
  assert.match(app, /blob\.size < 800/);
  assert.match(app, /naturalWidth < 200/);
  assert.match(app, /setInterval\(\(\) => \{ if \(!document\.hidden && state\.browserActive\) void refreshBrowserFrame\(\); \}, 240\)/);
  assert.match(app, /for \(const ms of \[160, 420, 900, 1500\]\)/);
  assert.match(await readFile(path.join(root, "src", "browser", "recorder.ts"), "utf8"), /watchLayerPaint|layerHotUntil|nudgeOverlayFrames/);
  assert.match(await readFile(path.join(root, "src", "browser", "page-actions.ts"), "utf8"), /nudgeOverlayFrames/);
  assert.doesNotMatch(await readFile(path.join(root, "src", "browser", "recorder.ts"), "utf8"), /waitForOpenedLayer|inspectLayerPaint/);
  assert.match(app, /setInterval\(\(\) => \{ if \(!document\.hidden\) void pollBrowserState\(\); \}, 2000\)/);
  assert.match(server, /Content-Type": "image\/jpeg"/);
  assert.doesNotMatch(app, /setInterval\(pollBrowser, 900\)/);
  assert.doesNotMatch(app, /setInterval\(\(\) => \{ if \(!document\.hidden\) void pollBrowser\(\); \}, 1400\)/);
});

test("workbench operations execute without a confirmation dialog", async () => {
  const [extension, bridge, page, app, browserSkill] = await Promise.all([
    readFile(path.join(root, ".pi", "extensions", "business-skill-studio.ts"), "utf8"),
    readFile(path.join(root, "src", "web", "pi-rpc.ts"), "utf8"),
    readFile(path.join(root, "src", "web", "workbench-page.ts"), "utf8"),
    readFile(path.join(root, "web", "app.js"), "utf8"),
    readFile(path.join(root, ".pi", "skills", "control-in-app-browser", "SKILL.md"), "utf8")
  ]);
  const browserControl = extension.match(/name: "business_browser_control"[\s\S]*?^\s{2}\}\);/m)?.[0] || "";

  assert.doesNotMatch(browserControl, /ctx\.ui\.confirm|Confirm real browser write/);
  assert.match(extension, /studio\.execute\(cap\.id, executionInput, cap\.confirmation\.required\)/);
  assert.doesNotMatch(extension, /Write operation cancelled by user|用户取消了 Skill 导出|Binding approval cancelled/);
  assert.match(bridge, /Execute ordinary browser actions and business operations immediately/);
  assert.match(bridge, /Login is the exception:[\s\S]*do not click or fill anything and do not retry/);
  assert.doesNotMatch(bridge, /must wait for the existing explicit confirmation dialog/);
  assert.match(page, /event\.method === "confirm"[\s\S]*respondToUi\(\{ id: event\.id, confirmed: true \}\)/);
  assert.match(app, /if \(request\.method === "confirm"\) \{\s*state\.currentUiRequest = request;\s*void closeConfirmation\(true\);/s);
  assert.match(browserSkill, /Login is never an automatic form attempt/);
  assert.match(browserSkill, /Login is the exception and must pause without any automatic attempt/);
  assert.match(browserSkill, /recentUserActions/);
  assert.match(browserSkill, /#el-id-\*/);
  assert.match(browserSkill, /YYYY-MM-DD/);
  assert.match(browserSkill, /todoFields|exercise-form/);
  assert.match(browserSkill, /first or second failed automatic operation is not a stop/);
  assert.match(browserSkill, /manual-takeover card/);
  assert.match(browserSkill, /resumedAfterManualTakeover/);
  assert.match(browserSkill, /authoritative whole-form action/);
  assert.match(browserControl, /fills every currently visible eligible field in one pass/);
  assert.match(browserControl, /first or second failure must be repaired/);
  assert.match(bridge, /todoFields|exercise-form|todoCount/);
  assert.match(bridge, /first or second failed automatic operation is not a stop/);
  assert.match(bridge, /never click the dim overlay|Never click text=2/);
  assert.match(bridge, /Windows|Never use bash|WSL/);
  assert.match(bridge, /exclude-tools[\s\S]*bash|The bash tool is disabled/);
  assert.match(bridge, /sessionId from record_stop|主能力 and 字段候选接口/);
  assert.match(bridge, /审核通过|re-record|re-analyze/);
  assert.match(bridge, /business_skill_export after record_stop/);
  assert.match(bridge, /owns analyze, review, repair, re-review, and export/);
  assert.match(bridge, /After every mutating business_browser_control call, inspect recordingAudit/);
  assert.doesNotMatch(bridge, /stop and report; do not analyze, validate, or record again/);
  assert.match(extension, /session\?\.blocked/);
  assert.match(extension, /requireConversationSession/);
  assert.match(extension, /当前对话还没有录制证据/);
  assert.match(extension, /lastRecordingSessionId = undefined/);
  assert.match(extension, /正式闭环以录制阶段的 recordingAudit 为准/);
  assert.doesNotMatch(extension, /停止并报告未通过原因/);
  const workflowSkill = await readFile(path.join(root, ".pi", "skills", "business-skill-studio", "SKILL.md"), "utf8");
  assert.match(workflowSkill, /Export owns Analyze → Review → Repair → Re-review → Export/);
  assert.doesNotMatch(workflowSkill, /This is the gate/);
  assert.match(extension, /本次识别主能力|本次录制主能力|已导出主能力/);
  assert.match(extension, /审核通过|审核未通过/);
  assert.match(await readFile(path.join(root, "src", "studio-service.ts"), "utf8"), /sessionCatalogSlice|reviewSession/);
  assert.match(await readFile(path.join(root, "src", "web", "workbench-page.ts"), "utf8"), /missingFields/);
  assert.doesNotMatch(extension, /details:\s*\{\s*review,\s*capabilities\s*\}/);
});

test("Windows Pi host uses powershell instead of bash", async () => {
  const [settings, bridge, studioSkill, browserSkill] = await Promise.all([
    readFile(path.join(root, ".pi", "settings.json"), "utf8"),
    readFile(path.join(root, "src", "web", "pi-rpc.ts"), "utf8"),
    readFile(path.join(root, ".pi", "skills", "business-skill-studio", "SKILL.md"), "utf8"),
    readFile(path.join(root, ".pi", "skills", "control-in-app-browser", "SKILL.md"), "utf8")
  ]);
  assert.match(settings, /"powershell"/);
  assert.doesNotMatch(settings, /"bash"/);
  assert.match(bridge, /exclude-tools["', ]+bash/);
  assert.match(bridge, /The bash tool is disabled/);
  assert.match(studioSkill, /bash tool is disabled|use powershell/);
  assert.match(browserSkill, /bash tool is disabled|use powershell/);
});

test("refresh keeps a tab session while a new page starts isolated", async () => {
  const [app, server, page] = await Promise.all([
    readFile(path.join(root, "web", "app.js"), "utf8"),
    readFile(path.join(root, "src", "web", "server.ts"), "utf8"),
    readFile(path.join(root, "src", "web", "workbench-page.ts"), "utf8")
  ]);

  assert.match(app, /sessionStorage\.getItem\(PAGE_SESSION_KEY\)/);
  assert.match(app, /sessionStorage\.setItem\(PAGE_SESSION_KEY/);
  assert.match(app, /X-Bss-Page-Session/);
  assert.match(app, /\/api\/events\?pageSession=/);
  assert.doesNotMatch(app, /localStorage\.(get|set)Item\(PAGE_SESSION_KEY/);
  assert.match(server, /function getOrCreatePage/);
  assert.match(server, /function requirePage/);
  assert.match(server, /x-bss-page-session/);
  assert.match(server, /pagesByToken/);
  assert.match(page, /class WorkbenchPage/);
  assert.match(page, /profileDir: this\.pageProfileDir/);
  assert.match(page, /seedPageProfile\(this\.sharedProfileDir, this\.pageProfileDir\)/);
  assert.match(page, /syncLoginState\(this\.pageProfileDir, this\.sharedProfileDir\)/);
  assert.match(page, /new PiRpcBridge/);
  assert.match(page, /scheduleAbandon/);
  assert.match(page, /async dispose\(/);
  assert.match(page, /async abortWork\(/);
  assert.match(server, /\/api\/session\/leave/);
  assert.match(server, /page\.scheduleAbandon\("sse-disconnected"\)/);
  assert.match(server, /page\.abortWork\("abort"\)/);
  assert.match(server, /page\.dispose\("studio-shutdown"\)/);
  assert.match(app, /function notifyPageLeave\(/);
  assert.match(app, /navigator\.sendBeacon/);
  assert.match(app, /pagehide/);
  assert.doesNotMatch(app, /window\.close\(/);
});

test("idle workbench tabs do not start Pi until the tab is watching or a task is sent", async () => {
  const [app, server] = await Promise.all([
    readFile(path.join(root, "web", "app.js"), "utf8"),
    readFile(path.join(root, "src", "web", "server.ts"), "utf8")
  ]);
  const creation = server.slice(server.indexOf("function getOrCreatePage"), server.indexOf("function pageSessionIdFrom"));
  const chatRoute = server.match(/pathname === "\/api\/chat"[\s\S]*?return;\s*\}/)?.[0] || "";
  const eventsRoute = server.match(/pathname === "\/api\/events"[\s\S]*?return;\s*\}/)?.[0] || "";
  assert.doesNotMatch(creation, /ensureStarted\(/);
  assert.match(eventsRoute, /ensureStarted\(/);
  assert.match(chatRoute, /acceptUserMessage|addUser/);
  assert.match(chatRoute, /sendJson\(response, 202/);
  assert.match(chatRoute, /runPrompt/);
  assert.doesNotMatch(chatRoute, /await page\.pi\.prompt|await page\.ensureStarted/);
  assert.match(app, /elements\.sendPrompt\.disabled = state\.agentAborting/);
  assert.doesNotMatch(app, /pendingPrompt/);
});

test("skill catalog distinguishes handbook export from business spec dump", async () => {
  const [html, app, css] = await Promise.all([
    readFile(path.join(root, "web", "index.html"), "utf8"),
    readFile(path.join(root, "web", "app.js"), "utf8"),
    readFile(path.join(root, "web", "styles.css"), "utf8")
  ]);
  assert.match(html, /执行手册，不是业务说明书/);
  assert.match(html, /只有审核通过才能导出/);
  assert.match(html, /dist\/skills/);
  assert.match(html, /独立目录，不会覆盖上一份/);
  assert.match(html, /SKILL.md 路由手册/);
  assert.match(html, /主能力索引/);
  assert.match(html, /规划例子与失败/);
  assert.match(html, /例如 采购订单/);
  assert.doesNotMatch(html, /PYTHON AGENT SKILLS|class="management-header"|<h1>Skill 目录<\/h1>/);
  assert.match(css, /\.management-view\s*\{[^}]*padding:\s*14px/s);
  assert.match(css, /\.recording-view\s*\{[^}]*padding:\s*0/s);
  assert.doesNotMatch(css, /\.management-header/);
  assert.match(html, /<th class="col-skill">Skill</);
  assert.match(html, /<th class="col-metric">能力</);
  assert.match(html, /<th class="col-metric">请求</);
  assert.match(html, /id="skills-sort-time"/);
  assert.match(html, /id="skills-pager"/);
  assert.match(html, /<th class="col-actions">操作</);
  assert.match(css, /grid-template-columns:\s*minmax\(0, 2\.2fr\) minmax\(72px, \.7fr\) minmax\(72px, \.7fr\) minmax\(140px, 1fr\) minmax\(220px, 1\.4fr\)/);
  assert.match(app, /function renderSkills\(\)/);
  assert.match(app, /function pagedSkills\(\)/);
  assert.match(app, /skillsPageSize: 8/);
  assert.match(app, /skillExportedAt/);
  assert.match(app, /已导出主能力/);
  assert.doesNotMatch(app, /复制路径|复制ID|navigator\.clipboard/);
  assert.doesNotMatch(html, /skills-board[\s\S]*录制/);
  assert.doesNotMatch(html, /skills-grid|skill-card/);
  assert.doesNotMatch(app, /skill-card|skill-facts/);
  assert.doesNotMatch(html, /\.business-skill-studio\/export/);
  assert.doesNotMatch(app, /\["业务能力"/);
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
