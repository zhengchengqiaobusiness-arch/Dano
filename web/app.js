import { completeRecordingSession } from "./recording-workflow.js";

const $ = (selector) => document.querySelector(selector);
const elements = {
  addressForm: $(".address-form"), browserUrl: $("#browser-url"), browserFrame: $("#browser-frame"),
  browserViewport: $("#browser-viewport"),
  manualModeHint: $("#manual-mode-hint"),
  browserStatus: $("#browser-status"), browserSize: $("#browser-size"),
  stopRecording: $("#stop-recording"),
  reloadBrowser: $("#reload-browser"), modelStatus: $("#model-status"),
  conversation: $("#conversation"), clearSession: $("#clear-session"), composer: $(".composer"), prompt: $("#prompt"),
  composerHint: $("#composer-hint"), sendPrompt: $("#send-prompt"), abortPrompt: $("#abort-prompt"),
  browserIme: $("#browser-ime"),
  exportForm: $("#export-form"),
  skillName: $("#skill-name"), skillsList: $("#skills-list"),
  skillsPager: $("#skills-pager"), skillsPageInfo: $("#skills-page-info"),
  skillsPrev: $("#skills-prev"), skillsNext: $("#skills-next"), skillsSortTime: $("#skills-sort-time"),
  confirmationModal: $("#confirmation-modal"), confirmationTitle: $("#confirmation-title"),
  confirmationMessage: $("#confirmation-message"), confirmationOptions: $("#confirmation-options"),
  confirmationInput: $("#confirmation-input"), confirmationEditor: $("#confirmation-editor"),
  confirmationCancel: $("#confirmation-cancel"), confirmationApprove: $("#confirmation-approve"),
  invokeModal: $("#invoke-modal"), invokeTitle: $("#invoke-title"), invokeGoal: $("#invoke-goal"),
  invokeCancel: $("#invoke-cancel"), invokeSubmit: $("#invoke-submit"), toast: $("#toast")
};

const PAGE_SESSION_KEY = "bss-page-session";
let memoryPageSession = "";

function createPageSessionId() {
  const raw = (globalThis.crypto?.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`).replace(/-/g, "");
  return `page_${raw}`;
}

function pageSessionId() {
  try {
    const existing = sessionStorage.getItem(PAGE_SESSION_KEY) || "";
    if (/^page_[A-Za-z0-9_-]{8,80}$/.test(existing)) return existing;
    const id = createPageSessionId();
    sessionStorage.setItem(PAGE_SESSION_KEY, id);
    return id;
  } catch {
    if (!memoryPageSession) memoryPageSession = createPageSessionId();
    return memoryPageSession;
  }
}

function pageHeaders(extra = {}) {
  return { "Content-Type": "application/json", "X-Bss-Page-Session": pageSessionId(), ...extra };
}

const state = {
  view: "recording", browserActive: false, browserMode: "automatic", agentReady: false, agentStreaming: false, agentAborting: false,
  currentUiRequest: null, localConfirmation: null, invokeSkill: null,
  sessionNodes: new Map(), toastTimer: null, skills: [], skillsPage: 1, skillsPageSize: 8, skillsSort: "desc",
  sessionFollow: false, sessionLive: true, sessionEpoch: 0,
  manualQueue: Promise.resolve(), manualRefreshTimers: [], recordingAction: null, clearingSession: false,
  pollInFlight: false, frameLoading: false, frameBlobUrl: null, lastFrameAt: 0, frameEpoch: 0,
  lastStatusText: "", viewport: { width: 1440, height: 960 },
  imeComposing: false, imeBuffer: "", imeTimer: null
};

async function api(path, options = {}) {
  const response = await fetch(path, { ...options, headers: pageHeaders(options.headers || {}) });
  const payload = response.status === 204 ? null : await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload?.error || `请求失败（${response.status}）`);
  return payload;
}

function showToast(message) {
  clearTimeout(state.toastTimer);
  elements.toast.textContent = message;
  elements.toast.hidden = false;
  state.toastTimer = setTimeout(() => { elements.toast.hidden = true; }, 4500);
}

function timeLabel(value = new Date().toISOString()) {
  return new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit" }).format(new Date(value));
}

function conversationAtBottom() {
  const scroller = elements.conversation;
  return scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight <= 2;
}

function syncSessionFollowFromUser() {
  state.sessionFollow = conversationAtBottom();
}

function followSessionIfWanted() {
  if (!state.sessionFollow) return;
  elements.conversation.scrollTop = elements.conversation.scrollHeight;
}

function setBrowserStatus(text, warn = false) {
  if (state.lastStatusText === text) return;
  state.lastStatusText = text;
  if (!elements.browserStatus) return;
  elements.browserStatus.innerHTML = "";
  const dot = document.createElement("i");
  dot.className = warn ? "status-dot warn" : /空闲/.test(text) ? "status-dot muted" : "status-dot";
  elements.browserStatus.append(dot, document.createTextNode(` ${text}`));
}

function setView(view) {
  state.view = view;
  document.querySelectorAll("[data-view]").forEach(button => button.classList.toggle("active", button.dataset.view === view));
  document.querySelectorAll("[data-view-panel]").forEach(panel => {
    const active = panel.dataset.viewPanel === view;
    panel.hidden = !active;
    panel.classList.toggle("active", active);
  });
  if (view === "skills") void loadSkills();
}

function toolLabel(name) {
  return ({ business_skill_record_start: "启动内置浏览器录制", business_skill_record_stop: "保存录制",
    business_browser_control: "操作内置浏览器", business_skill_analyze: "识别业务能力",
    business_skill_validate: "验证能力", business_skill_plan: "规划路线", business_skill_execute: "执行业务能力",
    business_skill_approve_binding: "确认数据绑定", business_skill_export: "导出 Python Skill",
    manual_page_input: "手动填写" })[name] || name;
}

function dataText(value) {
  if (value === undefined) return "等待输出…";
  if (typeof value === "string") return value;
  try { return JSON.stringify(value, null, 2); } catch { return String(value); }
}

function resetBrowserWorkbench() {
  state.browserActive = false;
  state.recordingAction = null;
  state.pollInFlight = false;
  state.frameLoading = false;
  state.frameEpoch += 1;
  for (const timer of state.manualRefreshTimers) clearTimeout(timer);
  state.manualRefreshTimers = [];
  state.manualQueue = Promise.resolve();
  state.imeComposing = false;
  state.imeBuffer = "";
  clearTimeout(state.imeTimer);
  if (elements.browserIme) elements.browserIme.value = "";
  if (elements.browserUrl) elements.browserUrl.value = "";
  if (elements.prompt) elements.prompt.value = "";
  clearBrowserFrame();
  elements.browserFrame.classList.remove("active");
  if (elements.reloadBrowser) elements.reloadBrowser.disabled = true;
  renderBrowserMode();
  renderRecordingActions();
}

function resetWorkbench() {
  state.sessionLive = false;
  for (const node of state.sessionNodes.values()) node.remove();
  state.sessionNodes.clear();
  elements.conversation.querySelectorAll("[data-session-id]").forEach(node => node.remove());
  if (state.localConfirmation) {
    const resolve = state.localConfirmation;
    state.localConfirmation = null;
    resolve(false);
  }
  state.currentUiRequest = null;
  elements.confirmationModal.hidden = true;
  if (elements.invokeModal) elements.invokeModal.hidden = true;
  state.invokeSkill = null;
  state.sessionFollow = false;
  elements.conversation.scrollTop = 0;
  updateAgentStatus(state.agentReady, false);
  resetBrowserWorkbench();
}

async function clearSessionHistory() {
  if (state.clearingSession) return;
  state.clearingSession = true;
  if (elements.clearSession) elements.clearSession.disabled = true;
  try {
    const result = await api("/api/session/clear", { method: "POST", body: "{}" });
    if (result?.epoch != null) state.sessionEpoch = result.epoch;
    resetWorkbench();
    await pollBrowser(true);
    showToast("已结束录制并清空全部内容；下一条消息是新对话");
  } catch (error) {
    showToast(error.message);
  } finally {
    state.clearingSession = false;
    if (elements.clearSession) elements.clearSession.disabled = false;
  }
}

function renderSessionItem(item) {
  const existing = state.sessionNodes.get(item.id);
  if (existing) existing.remove();
  let node;
  if (item.kind === "message") {
    const message = item;
    const article = document.createElement("article");
    article.className = `message ${message.role === "user" ? "user-message" : "assistant-message"}${message.complete ? "" : " streaming"}`;
    const meta = document.createElement("div"); meta.className = "message-meta";
    const author = document.createElement("span"); author.textContent = message.role === "user" ? "你" : "PI";
    const time = document.createElement("time"); time.textContent = timeLabel(message.at);
    const text = document.createElement("p"); text.className = "session-text"; text.textContent = message.text || "";
    meta.append(author, time); article.append(meta, text); node = article;
  } else if (item.kind === "thinking") {
    const details = document.createElement("details"); details.className = "thinking-block"; details.open = true;
    const summary = document.createElement("summary"); summary.textContent = item.complete ? "思考过程" : "正在思考…";
    const text = document.createElement("pre"); text.className = "session-text"; text.textContent = item.text || "";
    details.append(summary, text); node = details;
  } else {
    const details = document.createElement("details"); details.className = `tool-block phase-${item.phase}`; details.open = item.phase === "running";
    const summary = document.createElement("summary");
    const status = item.phase === "running" ? "执行中" : item.phase === "error" ? "未完成" : "已完成";
    summary.textContent = `${toolLabel(item.toolName)} · ${status}`;
    const argsTitle = document.createElement("strong"); argsTitle.textContent = "调用参数";
    const args = document.createElement("pre"); args.textContent = dataText(item.args);
    const resultTitle = document.createElement("strong"); resultTitle.textContent = "阶段输出";
    const result = document.createElement("pre"); result.textContent = dataText(item.result);
    details.append(summary, argsTitle, args, resultTitle, result); node = details;
  }
  node.dataset.sessionId = item.id;
  const scroller = elements.conversation;
  const keptTop = scroller.scrollTop;
  elements.conversation.append(node); state.sessionNodes.set(item.id, node);
  if (state.sessionFollow) scroller.scrollTop = scroller.scrollHeight;
  else scroller.scrollTop = keptTop;
  return node;
}

function patchSessionItem(event) {
  const node = state.sessionNodes.get(event.id); if (!node) return;
  const text = node.querySelector(".session-text");
  if (text && event.appendText) text.textContent += event.appendText;
  if (event.complete) {
    node.classList.remove("streaming");
    if (node.matches("details")) {
      if (!node.classList.contains("thinking-block")) node.open = false;
      if (node.classList.contains("thinking-block")) {
        node.open = true;
        node.querySelector("summary").textContent = "思考过程";
      }
    }
  }
  followSessionIfWanted();
}

function renderAgentControls() {
  const working = state.agentStreaming;
  elements.sendPrompt.disabled = !state.agentReady;
  elements.prompt.disabled = false;
  if (elements.abortPrompt) {
    elements.abortPrompt.disabled = !working || state.agentAborting;
    elements.abortPrompt.textContent = state.agentAborting ? "终止中" : "终止";
  }
  if (elements.composerHint) {
    elements.composerHint.textContent = working
      ? "Enter 发送跟进 · 终止可单独点"
      : "Enter 发送 · Shift+Enter 换行";
  }
}

function updateAgentStatus(ready, streaming) {
  state.agentReady = Boolean(ready); state.agentStreaming = Boolean(streaming);
  if (!state.agentStreaming) state.agentAborting = false;
  renderAgentControls();
}

function renderBrowserMode() {
  document.querySelectorAll("[data-browser-mode]").forEach(button => button.classList.toggle("active", button.dataset.browserMode === state.browserMode));
  elements.browserViewport.classList.toggle("interactive", state.browserActive);
  elements.manualModeHint.hidden = !state.browserActive;
  if (elements.manualModeHint) {
    elements.manualModeHint.textContent = state.browserMode === "manual"
      ? "手动录制 · 直接点击画面，滚轮可滚动"
      : "可直接点击画面操作，Pi 也可自动点击";
  }
}

function renderRecordingActions() {
  const busy = Boolean(state.recordingAction);
  elements.stopRecording.disabled = !state.browserActive || busy;
  elements.stopRecording.textContent = state.recordingAction === "stop" ? "正在结束…" : "结束录制";
}

async function changeBrowserMode(mode) {
  const result = await api("/api/browser/mode", { method: "POST", body: JSON.stringify({ mode }) });
  state.browserMode = result.mode || mode; renderBrowserMode(); await pollBrowser();
  showToast(mode === "manual" ? "已切换到手动录制：直接操作内置画面" : "已切换到 Pi 自动点击模式");
}

function clearBrowserFrame() {
  if (state.frameBlobUrl) {
    URL.revokeObjectURL(state.frameBlobUrl);
    state.frameBlobUrl = null;
  }
  elements.browserFrame.removeAttribute("src");
  state.lastFrameAt = 0;
  state.lastStatusText = "";
}

async function refreshBrowserFrame(force = false) {
  if (!state.browserActive || state.frameLoading || document.hidden) return;
  if (!force && Date.now() - state.lastFrameAt < 160) return;
  const epoch = state.frameEpoch;
  state.frameLoading = true;
  try {
    const response = await fetch(`/api/browser/frame?t=${Date.now()}`, { cache: "no-store", headers: pageHeaders(), signal: AbortSignal.timeout(1200) });
    if (epoch !== state.frameEpoch) return;
    if (response.status === 204 || !response.ok) return;
    const blob = await response.blob();
    if (!blob || blob.size < 80) return;
    const url = URL.createObjectURL(blob);
    const probe = new Image();
    probe.src = url;
    await (probe.decode ? probe.decode() : Promise.resolve()).catch(() => {});
    if (epoch !== state.frameEpoch) {
      URL.revokeObjectURL(url);
      return;
    }
    const previous = state.frameBlobUrl;
    state.frameBlobUrl = url;
    elements.browserFrame.src = url;
    elements.browserFrame.classList.add("active");
    if (previous && previous !== url) URL.revokeObjectURL(previous);
    state.lastFrameAt = Date.now();
  } catch {
    // Keep the last decoded frame so a hung capture does not blank the workbench.
  } finally {
    if (epoch === state.frameEpoch) state.frameLoading = false;
  }
}

async function pollBrowserState() {
  if (state.pollInFlight) return;
  const epoch = state.frameEpoch;
  state.pollInFlight = true;
  try {
    const browser = await api("/api/browser/state");
    if (epoch !== state.frameEpoch) return;
    state.browserActive = Boolean(browser.active);
    state.browserMode = browser.mode || state.browserMode;
    renderBrowserMode();
    elements.browserFrame.classList.toggle("active", state.browserActive);
    elements.reloadBrowser.disabled = !state.browserActive;
    renderRecordingActions();
    if (!state.browserActive) {
      clearBrowserFrame();
      setBrowserStatus("浏览器空闲");
      return;
    }
    if (document.activeElement !== elements.browserUrl && !String(browser.url || "").startsWith("chrome-error:")) {
      elements.browserUrl.value = browser.url || "";
    }
    if (browser.viewport) {
      state.viewport = browser.viewport;
      if (elements.browserSize) elements.browserSize.textContent = `${browser.viewport.width} × ${browser.viewport.height}`;
    }
    setBrowserStatus(browser.pageError || browser.title || "浏览器运行中", Boolean(browser.pageError));
  } catch {
    if (epoch === state.frameEpoch) setBrowserStatus("浏览器状态暂时不可用，可继续点击或刷新", true);
  } finally {
    if (epoch === state.frameEpoch) state.pollInFlight = false;
  }
}

async function pollBrowser(forceFrame = false) {
  await pollBrowserState();
  if (state.browserActive) await refreshBrowserFrame(forceFrame);
}

function previewPaneSize() {
  const box = elements.browserViewport?.getBoundingClientRect();
  const width = Math.round(box?.width || 0);
  const height = Math.round(box?.height || 0);
  if (width < 80 || height < 80) return null;
  return { width, height };
}

function syncPreviewViewport() {
  const size = previewPaneSize();
  if (!size || !state.browserActive) return;
  const current = state.viewport;
  if (current && Math.abs(current.width - size.width) < 8 && Math.abs(current.height - size.height) < 8) return;
  void api("/api/browser/viewport", { method: "POST", body: JSON.stringify(size) }).then(result => {
    if (result?.viewport) state.viewport = result.viewport;
    void refreshBrowserFrame(true);
  }).catch(() => {});
}

async function openBrowser(rawUrl) {
  const value = rawUrl.trim(); if (!value) return;
  const url = /^[a-z][a-z0-9+.-]*:\/\//i.test(value) ? value : `https://${value}`;
  await api("/api/browser/open", { method: "POST", body: JSON.stringify({ url, name: "web-session", mode: state.browserMode, viewport: previewPaneSize() }) });
  await pollBrowser();
  showToast("录制已开始");
}

async function completeRecording() {
  if (!state.browserActive || state.recordingAction) return;
  state.recordingAction = "stop";
  renderRecordingActions();
  try {
    const result = await completeRecordingSession(api);
    state.browserActive = false;
    await pollBrowser();
    showToast(`录制已停止，证据已保存：${result.session.id}`);
  } catch (error) {
    showToast(error.message);
  } finally {
    state.recordingAction = null;
    await pollBrowser();
  }
}

async function manualCommand(command) {
  if (!state.browserActive) return;
  await api("/api/browser/manual", { method: "POST", body: JSON.stringify(command) });
  for (const timer of state.manualRefreshTimers) clearTimeout(timer);
  state.manualRefreshTimers = [];
  void refreshBrowserFrame(true);
  for (const ms of [160, 420, 900, 1500]) {
    state.manualRefreshTimers.push(setTimeout(() => void refreshBrowserFrame(true), ms));
  }
}

function enqueueManualCommand(command) {
  state.manualQueue = state.manualQueue.then(() => manualCommand(command)).catch(error => showToast(error.message));
}

function displayedFrameRect() {
  const img = elements.browserFrame;
  const box = img.getBoundingClientRect();
  const sourceWidth = img.naturalWidth;
  const sourceHeight = img.naturalHeight;
  if (!sourceWidth || !sourceHeight || !box.width || !box.height) return null;
  const scale = Math.min(box.width / sourceWidth, box.height / sourceHeight);
  const width = sourceWidth * scale;
  const height = sourceHeight * scale;
  return {
    left: box.left + (box.width - width) / 2,
    top: box.top + (box.height - height) / 2,
    width,
    height
  };
}

function browserCoordinates(event) {
  const rect = displayedFrameRect();
  if (!rect) return null;
  if (event.clientX < rect.left || event.clientX > rect.left + rect.width || event.clientY < rect.top || event.clientY > rect.top + rect.height) return null;
  const viewW = state.viewport?.width || elements.browserFrame.naturalWidth;
  const viewH = state.viewport?.height || elements.browserFrame.naturalHeight;
  return {
    x: Math.round((event.clientX - rect.left) / rect.width * viewW),
    y: Math.round((event.clientY - rect.top) / rect.height * viewH)
  };
}

async function submitPrompt(message) {
  const text = message.trim();
  if (!text || !state.agentReady) return;
  elements.prompt.value = "";
  state.sessionLive = true;
  state.sessionFollow = true;
  followSessionIfWanted();
  updateAgentStatus(true, true);
  try { await api("/api/chat", { method: "POST", body: JSON.stringify({ message: text }) }); }
  catch (error) { updateAgentStatus(state.agentReady, state.agentStreaming); showToast(error.message); }
}

async function abortAgent() {
  if (!state.agentStreaming || state.agentAborting) return;
  state.agentAborting = true; renderAgentControls();
  try {
    await api("/api/agent/abort", { method: "POST", body: "{}" });
    updateAgentStatus(state.agentReady, false);
    showToast("已终止 Pi 当前任务");
  } catch (error) {
    state.agentAborting = false; renderAgentControls(); showToast(error.message);
  }
}

function resetConfirmation() {
  elements.confirmationOptions.hidden = true; elements.confirmationOptions.innerHTML = "";
  elements.confirmationInput.hidden = true; elements.confirmationEditor.hidden = true;
  elements.confirmationInput.value = ""; elements.confirmationEditor.value = "";
  elements.confirmationApprove.textContent = "确认执行"; elements.confirmationApprove.className = "danger-button";
}

function confirmAction(title, message, danger = true) {
  resetConfirmation(); elements.confirmationTitle.textContent = title; elements.confirmationMessage.textContent = message;
  elements.confirmationApprove.textContent = danger ? "确认执行" : "确认";
  elements.confirmationApprove.className = danger ? "danger-button" : "primary-button";
  elements.confirmationModal.hidden = false;
  return new Promise(resolve => { state.localConfirmation = resolve; });
}

function showUiRequest(request) {
  if (request.method === "notify") { showToast(request.message || "Pi 通知"); return; }
  if (request.method === "confirm") {
    state.currentUiRequest = request;
    void closeConfirmation(true);
    return;
  }
  resetConfirmation(); state.currentUiRequest = request;
  elements.confirmationTitle.textContent = request.title || "Pi 需要你的选择";
  elements.confirmationMessage.textContent = request.message || "请提供继续执行所需的信息。";
  elements.confirmationApprove.textContent = request.method === "confirm" ? "确认执行" : "提交";
  elements.confirmationApprove.className = request.method === "confirm" ? "danger-button" : "primary-button";
  if (request.method === "select") {
    elements.confirmationOptions.hidden = false;
    (request.options || []).forEach((option, index) => {
      const value = typeof option === "string" ? option : String(option.value ?? option.label ?? "");
      const label = typeof option === "string" ? option : String(option.label ?? option.value ?? "");
      const item = document.createElement("label"); item.className = "modal-option";
      const radio = document.createElement("input"); radio.type = "radio"; radio.name = "pi-option"; radio.value = value; radio.checked = index === 0;
      item.append(radio, document.createTextNode(label)); elements.confirmationOptions.append(item);
    });
  } else if (request.method === "input") {
    elements.confirmationInput.hidden = false; elements.confirmationInput.placeholder = request.placeholder || "";
    elements.confirmationInput.value = request.prefill || "";
  } else if (request.method === "editor") {
    elements.confirmationEditor.hidden = false; elements.confirmationEditor.placeholder = request.placeholder || "";
    elements.confirmationEditor.value = request.prefill || "";
  }
  elements.confirmationModal.hidden = false;
}

async function closeConfirmation(accepted) {
  if (state.localConfirmation) {
    const resolve = state.localConfirmation; state.localConfirmation = null; elements.confirmationModal.hidden = true; resolve(accepted); return;
  }
  const ui = state.currentUiRequest; if (!ui) return; state.currentUiRequest = null; elements.confirmationModal.hidden = true;
  const response = { id: ui.id };
  if (!accepted) response.cancelled = true;
  else if (ui.method === "confirm") response.confirmed = true;
  else if (ui.method === "select") response.value = elements.confirmationOptions.querySelector("input:checked")?.value || "";
  else if (ui.method === "input") response.value = elements.confirmationInput.value;
  else response.value = elements.confirmationEditor.value;
  try { await api("/api/agent/ui-response", { method: "POST", body: JSON.stringify(response) }); }
  catch (error) { showToast(error.message); }
}

async function loadSkills() {
  try { state.skills = (await api("/api/skills")).skills; renderSkills(); }
  catch (error) { showToast(error.message); }
}

function skillExportedAt(value) {
  if (!value) return "—";
  const at = new Date(value);
  if (Number.isNaN(at.getTime())) return "—";
  return new Intl.DateTimeFormat("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }).format(at);
}

function skillTimeValue(skill) {
  const value = Date.parse(skill.exportedAt || skill.updatedAt || "");
  return Number.isFinite(value) ? value : 0;
}

function sortedSkills() {
  const items = [...state.skills];
  items.sort((left, right) => state.skillsSort === "asc" ? skillTimeValue(left) - skillTimeValue(right) : skillTimeValue(right) - skillTimeValue(left));
  return items;
}

function pagedSkills() {
  const items = sortedSkills();
  const size = state.skillsPageSize;
  const pages = Math.max(1, Math.ceil(items.length / size));
  if (state.skillsPage > pages) state.skillsPage = pages;
  if (state.skillsPage < 1) state.skillsPage = 1;
  const start = (state.skillsPage - 1) * size;
  return { items: items.slice(start, start + size), total: items.length, pages, page: state.skillsPage };
}

function renderSkillsPager(total, pages, page) {
  if (!elements.skillsPager) return;
  const many = total > state.skillsPageSize;
  elements.skillsPager.hidden = !many;
  if (elements.skillsPageInfo) elements.skillsPageInfo.textContent = many ? `第 ${page} / ${pages} 页 · ${total} 条` : "";
  if (elements.skillsPrev) elements.skillsPrev.disabled = page <= 1;
  if (elements.skillsNext) elements.skillsNext.disabled = page >= pages;
}

function renderSkills() {
  elements.skillsList.innerHTML = "";
  if (elements.skillsSortTime) {
    elements.skillsSortTime.dataset.sort = state.skillsSort;
    elements.skillsSortTime.setAttribute("aria-pressed", state.skillsSort === "desc" ? "true" : "false");
    elements.skillsSortTime.textContent = state.skillsSort === "asc" ? "产出时间 ↑" : "产出时间 ↓";
    elements.skillsSortTime.closest("th")?.setAttribute("aria-sort", state.skillsSort === "asc" ? "ascending" : "descending");
  }
  if (!state.skills.length) {
    const empty = document.createElement("tr");
    empty.innerHTML = "<td class=\"skills-empty-cell\" colspan=\"5\"><div class=\"empty-skills\"><strong>还没有导出的 Skill</strong><span>先完成录制并由 Pi 产出后，再从上方导出。</span></div></td>";
    elements.skillsList.append(empty);
    renderSkillsPager(0, 1, 1);
    return;
  }
  const { items, total, pages, page } = pagedSkills();
  renderSkillsPager(total, pages, page);
  for (const skill of items) {
    const row = document.createElement("tr");
    row.className = `skill-row ${skill.artifactStatus === "missing" ? "missing" : ""}`;
    const nameCell = document.createElement("td");
    nameCell.className = "col-skill";
    const title = document.createElement("div");
    title.className = "skill-title";
    const name = document.createElement("strong");
    name.textContent = skill.displayName;
    const status = document.createElement("span");
    status.className = `skill-status ${skill.status}`;
    status.textContent = skill.status === "frozen" ? "已冻结" : "可用";
    title.append(name, status);
    const meta = document.createElement("div");
    meta.className = "skill-meta";
    const slug = document.createElement("code");
    slug.textContent = `v${skill.version} · ${skill.name}`;
    meta.append(slug);
    nameCell.append(title, meta);
    const capability = document.createElement("td");
    capability.className = "col-metric";
    capability.textContent = String(skill.primaryCount ?? skill.primaryCapabilityIds?.length ?? skill.capabilityIds.length);
    const requests = document.createElement("td");
    requests.className = "col-metric";
    requests.textContent = String(skill.lookupCount ?? skill.lookupCapabilityIds?.length ?? 0);
    const produced = document.createElement("td");
    produced.className = "col-time";
    produced.textContent = skillExportedAt(skill.exportedAt);
    const actions = document.createElement("td");
    actions.className = "col-actions";
    [["调用", "invoke", false], ["重新导出", "reexport", false], [skill.status === "frozen" ? "解除冻结" : "冻结", "freeze", false], ["删除", "delete", true]].forEach(([label, action, danger]) => {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = label;
      button.className = danger ? "text-danger" : "";
      if (action === "reexport") button.title = "再导出一份新的唯一目录，不覆盖现有成品";
      button.addEventListener("click", () => void skillAction(skill, action));
      actions.append(button);
    });
    row.append(nameCell, capability, requests, produced, actions);
    elements.skillsList.append(row);
  }
}

async function exportSkill(name) {
  if (!(await confirmAction("导出 Python Skill", `将把当前全部已验证能力导出为“${name}”。会生成新的唯一目录，不会覆盖已有成品。未验证能力不会进入包。是否继续？`, false))) return;
  const result = await api("/api/skills/export", { method: "POST", body: JSON.stringify({ name, confirmed: true }) });
  showToast(`已导出主能力 ${result.primaryCount ?? 0} 项、字段候选 ${result.lookupCount ?? 0} 个（${result.name}），共 ${result.fileCount ?? 0} 个文件：${result.directory}`); await loadSkills();
}

async function skillAction(skill, action) {
  try {
    if (action === "invoke") {
      state.invokeSkill = skill; elements.invokeTitle.textContent = `调用 ${skill.displayName}`; elements.invokeGoal.value = ""; elements.invokeModal.hidden = false; elements.invokeGoal.focus(); return;
    }
    if (action === "reexport") { await exportSkill(skill.displayName); return; }
    if (action === "freeze") {
      const frozen = skill.status !== "frozen";
      if (!(await confirmAction(frozen ? "冻结 Skill" : "解除冻结", frozen ? "冻结后仍可从上方再导出一份新的唯一目录；本份成品保持不变。" : "解除后仍可调用这份成品。", false))) return;
      await api(`/api/skills/${encodeURIComponent(skill.name)}/freeze`, { method: "POST", body: JSON.stringify({ frozen, confirmed: true }) });
      showToast(frozen ? "Skill 已冻结" : "Skill 已解除冻结"); await loadSkills(); return;
    }
    if (action === "delete") {
      if (!(await confirmAction("删除 Skill", `将从目录移除“${skill.displayName}”。文件会移到项目回收区，可人工恢复。`, true))) return;
      await api(`/api/skills/${encodeURIComponent(skill.name)}/delete`, { method: "DELETE", body: JSON.stringify({ confirmed: true }) });
      showToast("Skill 已移到项目回收区"); await loadSkills();
    }
  } catch (error) {
    showToast(/EPERM|EACCES|EBUSY|ENOTEMPTY|operation not permitted|rename/i.test(error.message)
      ? "成品目录正被占用，已从目录移除。关闭占用该文件夹的程序后可再清理。"
      : error.message);
  }
}

function connectEvents() {
  const stream = new EventSource(`/api/events?pageSession=${encodeURIComponent(pageSessionId())}`);
  stream.onmessage = message => {
    const event = JSON.parse(message.data);
    if (event.type === "agent_status") updateAgentStatus(event.ready, event.streaming);
    if (event.type === "session_reset") {
      if (event.epoch != null) state.sessionEpoch = event.epoch;
      resetWorkbench();
      void pollBrowser(true);
      return;
    }
    if (event.epoch != null && event.epoch !== state.sessionEpoch) return;
    if ((event.type === "session_item" || event.type === "session_patch" || event.type === "session_replace" || event.type === "ui_request") && !state.sessionLive) return;
    if (event.type === "session_item") renderSessionItem(event.item);
    if (event.type === "session_patch") patchSessionItem(event);
    if (event.type === "session_replace") renderSessionItem(event.item);
    if (event.type === "ui_request") showUiRequest(event);
    if (event.type === "browser_changed") void pollBrowser(true);
    if (event.type === "browser_mode") { state.browserMode = event.mode; renderBrowserMode(); }
    if (event.type === "skills_changed" && state.view === "skills") void loadSkills();
    if (event.type === "studio_shutdown") showToast("Studio 服务已停止，页面保留");
    if (event.type === "agent_error") showToast(event.message || "Pi 连接异常");
  };
  stream.onerror = () => {
    updateAgentStatus(false, false);
    void fetch("/api/status", { cache: "no-store", headers: pageHeaders() }).catch(() => showToast("Studio 服务已断开，页面保留"));
  };
}

document.querySelectorAll("[data-view]").forEach(button => button.addEventListener("click", () => setView(button.dataset.view)));
document.querySelectorAll("[data-browser-mode]").forEach(button => button.addEventListener("click", () => void changeBrowserMode(button.dataset.browserMode).catch(error => showToast(error.message))));
elements.addressForm.addEventListener("submit", event => { event.preventDefault(); void openBrowser(elements.browserUrl.value).catch(error => showToast(error.message)); });
elements.reloadBrowser.addEventListener("click", () => state.browserActive && void api("/api/browser/reload", { method: "POST", body: "{}" }).then(pollBrowser).catch(error => showToast(error.message)));
elements.stopRecording.addEventListener("click", () => void completeRecording());
elements.clearSession.addEventListener("click", () => void clearSessionHistory());
elements.conversation.addEventListener("scroll", syncSessionFollowFromUser, { passive: true });
elements.conversation.addEventListener("wheel", syncSessionFollowFromUser, { passive: true });
elements.composer.addEventListener("submit", event => {
  event.preventDefault();
  void submitPrompt(elements.prompt.value);
});
if (elements.abortPrompt) elements.abortPrompt.addEventListener("click", () => void abortAgent());
elements.prompt.addEventListener("keydown", event => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); elements.composer.requestSubmit(); } });
document.querySelectorAll("[data-prompt]").forEach(button => button.addEventListener("click", () => void submitPrompt(button.dataset.prompt || "")));
elements.confirmationCancel.addEventListener("click", () => void closeConfirmation(false)); elements.confirmationApprove.addEventListener("click", () => void closeConfirmation(true));
elements.exportForm.addEventListener("submit", event => { event.preventDefault(); void exportSkill(elements.skillName.value.trim()).catch(error => showToast(error.message)); });
elements.skillsSortTime?.addEventListener("click", () => {
  state.skillsSort = state.skillsSort === "desc" ? "asc" : "desc";
  state.skillsPage = 1;
  renderSkills();
});
elements.skillsPrev?.addEventListener("click", () => {
  if (state.skillsPage <= 1) return;
  state.skillsPage -= 1;
  renderSkills();
});
elements.skillsNext?.addEventListener("click", () => {
  state.skillsPage += 1;
  renderSkills();
});
elements.invokeCancel.addEventListener("click", () => { elements.invokeModal.hidden = true; state.invokeSkill = null; });
elements.invokeSubmit.addEventListener("click", async () => {
  if (!state.invokeSkill || !elements.invokeGoal.value.trim()) return showToast("请先描述业务目标");
  try {
    await api(`/api/skills/${encodeURIComponent(state.invokeSkill.name)}/invoke`, { method: "POST", body: JSON.stringify({ goal: elements.invokeGoal.value }) });
    state.sessionLive = true;
    elements.invokeModal.hidden = true; state.invokeSkill = null; setView("recording"); showToast("已交给 Pi，执行中的选择和确认会显示在这里");
  } catch (error) { showToast(error.message); }
});
function flushImeText() {
  clearTimeout(state.imeTimer);
  const value = state.imeBuffer;
  state.imeBuffer = "";
  if (elements.browserIme) elements.browserIme.value = "";
  if (value) enqueueManualCommand({ action: "text", value });
}

function focusBrowserIme(event) {
  if (!elements.browserIme) return;
  elements.browserIme.style.left = `${event.clientX}px`;
  elements.browserIme.style.top = `${event.clientY}px`;
  elements.browserIme.focus();
}

elements.browserFrame.addEventListener("click", event => {
  if (!state.browserActive) return;
  const point = browserCoordinates(event); if (!point) return;
  flushImeText();
  focusBrowserIme(event);
  enqueueManualCommand({ action: "click", ...point });
});
elements.browserViewport.addEventListener("wheel", event => {
  if (!state.browserActive) return;
  event.preventDefault();
  const factor = event.deltaMode === WheelEvent.DOM_DELTA_LINE ? 40 : event.deltaMode === WheelEvent.DOM_DELTA_PAGE ? 600 : 1;
  enqueueManualCommand({ action: "scroll", deltaX: event.deltaX * factor, deltaY: event.deltaY * factor });
}, { passive: false });
if (elements.browserIme) {
  elements.browserIme.addEventListener("compositionstart", () => { state.imeComposing = true; });
  elements.browserIme.addEventListener("compositionend", event => {
    state.imeComposing = false;
    if (event.data) state.imeBuffer += event.data;
    flushImeText();
  });
  elements.browserIme.addEventListener("input", event => {
    if (state.imeComposing) return;
    const next = event.target.value || "";
    if (!next) return;
    state.imeBuffer += next;
    event.target.value = "";
    clearTimeout(state.imeTimer);
    state.imeTimer = setTimeout(flushImeText, 80);
  });
  elements.browserIme.addEventListener("keydown", event => {
    if (!state.browserActive) return;
    if (state.imeComposing) return;
    if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) return;
    event.preventDefault();
    flushImeText();
    const keyName = event.key === " " ? "Space" : event.key;
    const modifiers = [event.ctrlKey && "Control", event.altKey && "Alt", event.shiftKey && "Shift", event.metaKey && "Meta"].filter(Boolean);
    enqueueManualCommand({ action: "key", key: [...modifiers, keyName].join("+") });
  });
}
elements.browserViewport.addEventListener("keydown", event => {
  if (!state.browserActive || event.target === elements.browserIme) return;
  if (["Control", "Shift", "Alt", "Meta"].includes(event.key)) return;
  if (state.imeComposing) return;
  event.preventDefault();
  if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
    state.imeBuffer += event.key;
    clearTimeout(state.imeTimer);
    state.imeTimer = setTimeout(flushImeText, 80);
    return;
  }
  flushImeText();
  const keyName = event.key === " " ? "Space" : event.key;
  const modifiers = [event.ctrlKey && "Control", event.altKey && "Alt", event.shiftKey && "Shift", event.metaKey && "Meta"].filter(Boolean);
  enqueueManualCommand({ action: "key", key: [...modifiers, keyName].join("+") });
});
async function initialize() {
  try {
    const status = await api("/api/status"); updateAgentStatus(status.agent.ready, status.agent.streaming);
    elements.modelStatus.textContent = `${status.model || "由提供商选择模型"} · ${status.thinking}`;
    for (const item of status.sessionItems || []) renderSessionItem(item);
    state.browserMode = status.browser?.mode || state.browserMode; renderBrowserMode(); await pollBrowser();
  } catch (error) { showToast(error.message); }
  connectEvents();
  if (elements.browserViewport && typeof ResizeObserver === "function") {
    const observer = new ResizeObserver(() => syncPreviewViewport());
    observer.observe(elements.browserViewport);
  }
  window.addEventListener("resize", () => syncPreviewViewport());
  setInterval(() => { if (!document.hidden && state.browserActive) void refreshBrowserFrame(); }, 240);
  setInterval(() => { if (!document.hidden) void pollBrowserState(); }, 2000);
}

void initialize();
