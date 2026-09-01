const $ = (selector) => document.querySelector(selector);
const elements = {
  addressForm: $(".address-form"), browserUrl: $("#browser-url"), browserFrame: $("#browser-frame"),
  browserViewport: $("#browser-viewport"), manualControls: $("#manual-controls"), manualText: $("#manual-text"),
  manualModeHint: $("#manual-mode-hint"),
  browserStatus: $("#browser-status"), browserSize: $("#browser-size"), recordingState: $("#recording-state"),
  reloadBrowser: $("#reload-browser"), agentStatus: $("#agent-status"), modelStatus: $("#model-status"),
  conversation: $("#conversation"), composer: $(".composer"), prompt: $("#prompt"), sendPrompt: $("#send-prompt"),
  catalogSummary: $("#catalog-summary"), capabilityCount: $("#capability-count"), capabilityList: $("#capability-list"),
  emptyCapability: $("#empty-capability"), capabilityForm: $("#capability-form"), capabilityKind: $("#capability-kind"),
  capabilityId: $("#capability-id"), capabilityValidation: $("#capability-validation"), capabilityTitle: $("#capability-title"),
  capabilityOperation: $("#capability-operation"), capabilityDescription: $("#capability-description"),
  capabilityTransport: $("#capability-transport"), capabilityConfirmation: $("#capability-confirmation"),
  fieldTableBody: $("#field-table-body"), bindingList: $("#binding-list"), validationList: $("#validation-list"),
  addBinding: $("#add-binding"), addCandidate: $("#add-candidate"), mappingModal: $("#mapping-modal"),
  mappingTitle: $("#mapping-title"), mappingDescription: $("#mapping-description"), mappingForm: $("#mapping-form"),
  mappingSource: $("#mapping-source"), mappingValueLabel: $("#mapping-value-label"), mappingValuePath: $("#mapping-value-path"),
  mappingLabelRow: $("#mapping-label-row"), mappingLabelPath: $("#mapping-label-path"), mappingTarget: $("#mapping-target"),
  mappingNoteRow: $("#mapping-note-row"), mappingNote: $("#mapping-note"), mappingCancel: $("#mapping-cancel"),
  analyzeCatalog: $("#analyze-catalog"), validateCatalog: $("#validate-catalog"), exportForm: $("#export-form"),
  skillName: $("#skill-name"), skillsList: $("#skills-list"),
  confirmationModal: $("#confirmation-modal"), confirmationTitle: $("#confirmation-title"),
  confirmationMessage: $("#confirmation-message"), confirmationOptions: $("#confirmation-options"),
  confirmationInput: $("#confirmation-input"), confirmationEditor: $("#confirmation-editor"),
  confirmationCancel: $("#confirmation-cancel"), confirmationApprove: $("#confirmation-approve"),
  invokeModal: $("#invoke-modal"), invokeTitle: $("#invoke-title"), invokeGoal: $("#invoke-goal"),
  invokeCancel: $("#invoke-cancel"), invokeSubmit: $("#invoke-submit"), toast: $("#toast"),
  runtimeLogLines: $("#runtime-log-lines"), logCount: $("#log-count"), copyLogs: $("#copy-logs")
};

const operationLabels = { query: "查询", create: "新建", update: "修改", review: "审核", delete: "删除", authenticate: "认证", upload: "上传", download: "下载", action: "业务动作", unknown: "未识别" };
const sourceLabels = { caller: "调用方提供", fixed: "固定规则", session: "会话环境", generated: "运行时生成", computed: "计算得到", binding: "上游绑定", system: "业务系统处理" };
const state = {
  view: "recording", browserActive: false, browserMode: "automatic", agentReady: false, agentStreaming: false,
  currentAssistant: null, currentUiRequest: null, localConfirmation: null, invokeSkill: null,
  seenMessages: new Set(), toolRows: new Map(), toastTimer: null,
  catalog: { capabilities: [], routes: [], summary: {} }, selectedCapabilityId: null, skills: [], mappingMode: null,
  runtimeLogs: [], seenRuntimeLogs: new Set(), manualQueue: Promise.resolve(), manualRefreshTimer: null
};

async function api(path, options = {}) {
  const response = await fetch(path, { ...options, headers: { "Content-Type": "application/json", ...(options.headers || {}) } });
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

function setView(view) {
  state.view = view;
  document.querySelectorAll("[data-view]").forEach(button => button.classList.toggle("active", button.dataset.view === view));
  document.querySelectorAll("[data-view-panel]").forEach(panel => {
    const active = panel.dataset.viewPanel === view;
    panel.hidden = !active;
    panel.classList.toggle("active", active);
  });
  if (view === "catalog") void loadCatalog();
  if (view === "skills") void loadSkills();
  if (view === "logs") void loadLogs();
}

function appendRuntimeLog(entry) {
  if (!entry || state.seenRuntimeLogs.has(entry.id)) return;
  state.seenRuntimeLogs.add(entry.id); state.runtimeLogs.push(entry);
  const line = document.createElement("div"); line.className = `runtime-log-line level-${String(entry.level || "info").toLowerCase()}`;
  const time = document.createElement("time"); time.textContent = new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }).format(new Date(entry.at));
  const text = document.createElement("span"); text.textContent = entry.line || "";
  line.append(time, text); elements.runtimeLogLines.append(line);
  elements.logCount.textContent = `${state.runtimeLogs.length} 行`;
  elements.runtimeLogLines.scrollTop = elements.runtimeLogLines.scrollHeight;
}

async function loadLogs() {
  try { for (const entry of (await api("/api/logs")).logs || []) appendRuntimeLog(entry); }
  catch (error) { showToast(error.message); }
}

function appendMessage(message) {
  if (message.id && state.seenMessages.has(message.id)) return null;
  if (message.id) state.seenMessages.add(message.id);
  const article = document.createElement("article");
  article.className = `message ${message.role === "user" ? "user-message" : "assistant-message"}`;
  const meta = document.createElement("div"); meta.className = "message-meta";
  const author = document.createElement("span"); author.textContent = message.role === "user" ? "你" : "PI";
  const time = document.createElement("time"); time.textContent = timeLabel(message.at);
  const text = document.createElement("p"); text.textContent = message.text || "";
  meta.append(author, time); article.append(meta, text); elements.conversation.append(article);
  elements.conversation.scrollTop = elements.conversation.scrollHeight;
  return article;
}

function beginAssistantMessage() {
  if (state.currentAssistant) return;
  state.currentAssistant = appendMessage({ id: `stream-${Date.now()}`, role: "assistant", text: "", at: new Date().toISOString() });
  state.currentAssistant?.classList.add("streaming");
}

function appendAssistantDelta(delta) {
  beginAssistantMessage();
  const paragraph = state.currentAssistant?.querySelector("p");
  if (paragraph) paragraph.textContent += delta;
  elements.conversation.scrollTop = elements.conversation.scrollHeight;
}

function finishAssistantMessage(message) {
  if (state.currentAssistant) {
    state.currentAssistant.classList.remove("streaming");
    const paragraph = state.currentAssistant.querySelector("p");
    if (paragraph) paragraph.textContent = message.text || paragraph.textContent;
    state.seenMessages.add(message.id); state.currentAssistant = null;
  } else appendMessage(message);
}

function toolLabel(name) {
  return ({ business_skill_record_start: "启动内置浏览器录制", business_skill_record_stop: "保存录制",
    business_browser_control: "操作内置浏览器", business_skill_analyze: "识别原子能力",
    business_skill_validate: "验证能力", business_skill_plan: "规划路线", business_skill_execute: "执行业务能力",
    business_skill_approve_binding: "确认数据绑定", business_skill_export: "导出 Python Skill" })[name] || name;
}

function updateTool(event) {
  if (event.phase === "start") {
    const row = document.createElement("div"); row.className = "tool-activity"; row.textContent = `Pi 正在${toolLabel(event.toolName)}…`;
    elements.conversation.append(row); state.toolRows.set(event.toolCallId, row); return;
  }
  const row = state.toolRows.get(event.toolCallId);
  if (row) {
    row.textContent = event.isError ? `${toolLabel(event.toolName)}未完成` : `${toolLabel(event.toolName)}已完成`;
    row.style.borderColor = event.isError ? "var(--red)" : "var(--teal)"; state.toolRows.delete(event.toolCallId);
  }
}

function updateAgentStatus(ready, streaming) {
  state.agentReady = Boolean(ready); state.agentStreaming = Boolean(streaming); elements.agentStatus.innerHTML = "";
  const dot = document.createElement("i"); dot.className = `status-dot${ready ? "" : " muted"}`;
  elements.agentStatus.append(dot, document.createTextNode(streaming ? " Pi 工作中" : ready ? " Pi 已就绪" : " Pi 不可用"));
  elements.sendPrompt.disabled = !ready;
}

function renderBrowserMode() {
  document.querySelectorAll("[data-browser-mode]").forEach(button => button.classList.toggle("active", button.dataset.browserMode === state.browserMode));
  const manual = state.browserMode === "manual";
  elements.manualControls.hidden = !manual;
  elements.manualModeHint.hidden = !(manual && state.browserActive);
  elements.browserViewport.classList.toggle("manual", manual && state.browserActive);
  elements.addressForm.querySelector('button[type="submit"]').textContent = manual ? "接入并手动录制" : "接入并交给 Pi";
}

async function changeBrowserMode(mode) {
  const result = await api("/api/browser/mode", { method: "POST", body: JSON.stringify({ mode }) });
  state.browserMode = result.mode || mode; renderBrowserMode(); await pollBrowser();
  showToast(mode === "manual" ? "已切换到手动录制：直接操作内置画面" : "已切换到 Pi 自动点击模式");
}

async function pollBrowser() {
  try {
    const browser = await api("/api/browser/state"); state.browserActive = Boolean(browser.active); state.browserMode = browser.mode || state.browserMode; renderBrowserMode();
    elements.browserFrame.classList.toggle("active", state.browserActive); elements.recordingState.classList.toggle("active", state.browserActive);
    elements.recordingState.innerHTML = `<i></i>${state.browserActive ? " 正在录制" : " 等待会话"}`; elements.reloadBrowser.disabled = !state.browserActive;
    if (!state.browserActive) {
      elements.browserFrame.removeAttribute("src"); elements.browserStatus.innerHTML = '<i class="status-dot muted"></i> 浏览器空闲'; return;
    }
    if (document.activeElement !== elements.browserUrl) elements.browserUrl.value = browser.url || "";
    elements.browserStatus.innerHTML = ""; const dot = document.createElement("i"); dot.className = "status-dot";
    elements.browserStatus.append(dot, document.createTextNode(` ${browser.title || "浏览器运行中"}`));
    if (browser.viewport) elements.browserSize.textContent = `${browser.viewport.width} × ${browser.viewport.height}`;
    elements.browserFrame.src = `/api/browser/frame?t=${Date.now()}`;
  } catch { elements.browserStatus.textContent = "浏览器不可用"; }
}

async function openBrowser(rawUrl) {
  const value = rawUrl.trim(); if (!value) return;
  const url = /^[a-z][a-z0-9+.-]*:\/\//i.test(value) ? value : `https://${value}`;
  elements.recordingState.textContent = "正在启动…";
  await api("/api/browser/open", { method: "POST", body: JSON.stringify({ url, name: "web-session", mode: state.browserMode }) });
  await pollBrowser();
}

async function manualCommand(command) {
  if (!state.browserActive || state.browserMode !== "manual") return;
  await api("/api/browser/manual", { method: "POST", body: JSON.stringify(command) });
  clearTimeout(state.manualRefreshTimer);
  state.manualRefreshTimer = setTimeout(() => void pollBrowser(), 120);
}

function enqueueManualCommand(command) {
  state.manualQueue = state.manualQueue.then(() => manualCommand(command)).catch(error => showToast(error.message));
}

function browserCoordinates(event) {
  const rect = elements.browserFrame.getBoundingClientRect();
  const sourceWidth = elements.browserFrame.naturalWidth;
  const sourceHeight = elements.browserFrame.naturalHeight;
  if (!sourceWidth || !sourceHeight || !rect.width || !rect.height) return null;
  const scale = Math.min(rect.width / sourceWidth, rect.height / sourceHeight);
  const renderedWidth = sourceWidth * scale;
  const renderedHeight = sourceHeight * scale;
  const left = rect.left + (rect.width - renderedWidth) / 2;
  const top = rect.top + (rect.height - renderedHeight) / 2;
  if (event.clientX < left || event.clientX > left + renderedWidth || event.clientY < top || event.clientY > top + renderedHeight) return null;
  return { x: Math.round((event.clientX - left) / scale), y: Math.round((event.clientY - top) / scale) };
}

async function submitPrompt(message) {
  const text = message.trim(); if (!text) return; elements.prompt.value = "";
  try { await api("/api/chat", { method: "POST", body: JSON.stringify({ message: text }) }); }
  catch (error) { showToast(error.message); }
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

function statCard(label, value, note) {
  const card = document.createElement("article"); card.className = "summary-card";
  const valueNode = document.createElement("strong"); valueNode.textContent = String(value ?? 0);
  const labelNode = document.createElement("span"); labelNode.textContent = label;
  const noteNode = document.createElement("small"); noteNode.textContent = note;
  card.append(valueNode, labelNode, noteNode); return card;
}

async function loadCatalog() {
  try { state.catalog = await api("/api/catalog"); renderCatalog(); }
  catch (error) { showToast(error.message); }
}

function renderCatalog() {
  const { capabilities, summary } = state.catalog;
  elements.catalogSummary.replaceChildren(
    statCard("原子能力", summary.total, "来自真实请求分组"), statCard("已验证", summary.verified, "允许导出"),
    statCard("待处理", summary.candidates, "需要修正或补证"), statCard("字段", summary.fields, "均记录来源和处理方"),
    statCard("已确认绑定", summary.approvedBindings, "可自动组合")
  );
  elements.capabilityCount.textContent = `${capabilities.length} 项`; elements.capabilityList.innerHTML = "";
  if (!capabilities.some(item => item.id === state.selectedCapabilityId)) state.selectedCapabilityId = capabilities[0]?.id || null;
  for (const capability of capabilities) {
    const button = document.createElement("button"); button.type = "button";
    button.className = `capability-card${capability.id === state.selectedCapabilityId ? " active" : ""}`;
    const top = document.createElement("div"); const kind = document.createElement("span"); kind.textContent = operationLabels[capability.operation];
    const status = document.createElement("i"); status.textContent = capability.validation.status === "verified" ? "已验证" : "待验证";
    top.append(kind, status); const title = document.createElement("strong"); title.textContent = capability.title;
    const desc = document.createElement("p"); desc.textContent = capability.description;
    const meta = document.createElement("small"); meta.textContent = `${capability.inputForm.length} 个字段 · ${capability.bindings.filter(item => item.approved).length} 个绑定`;
    button.append(top, title, desc, meta); button.addEventListener("click", () => { state.selectedCapabilityId = capability.id; renderCatalog(); });
    elements.capabilityList.append(button);
  }
  renderCapabilityDetail(capabilities.find(item => item.id === state.selectedCapabilityId));
}

function select(options, value, className, dataset = {}) {
  const node = document.createElement("select"); node.className = className; Object.assign(node.dataset, dataset);
  for (const [optionValue, label] of Object.entries(options)) {
    const option = document.createElement("option"); option.value = optionValue; option.textContent = label; option.selected = optionValue === value; node.append(option);
  }
  return node;
}

function renderCapabilityDetail(capability) {
  elements.emptyCapability.hidden = Boolean(capability); elements.capabilityForm.hidden = !capability; if (!capability) return;
  elements.capabilityKind.textContent = `${operationLabels[capability.operation]} · 原子能力`;
  elements.capabilityId.textContent = capability.id; elements.capabilityTitle.value = capability.title;
  elements.capabilityOperation.value = capability.operation; elements.capabilityDescription.value = capability.description;
  elements.capabilityValidation.textContent = capability.validation.status === "verified" ? "证据已验证" : "等待验证";
  elements.capabilityValidation.className = `validation-badge ${capability.validation.status}`;
  elements.capabilityTransport.textContent = `${capability.transport.method} ${capability.transport.pathTemplate}`;
  elements.capabilityConfirmation.textContent = capability.confirmation.required ? "写操作 · 执行前确认" : "只读操作";
  elements.fieldTableBody.innerHTML = "";
  for (const field of capability.inputForm) {
    const row = document.createElement("tr"); row.dataset.path = field.path;
    const pathCell = document.createElement("td"); const pathCode = document.createElement("code"); pathCode.textContent = field.path; pathCell.append(pathCode);
    const labelCell = document.createElement("td"); const label = document.createElement("input"); label.value = field.label; label.dataset.prop = "label"; labelCell.append(label);
    const typeCell = document.createElement("td"); typeCell.append(select({ string: "文本", number: "数字", integer: "整数", boolean: "布尔", array: "数组", object: "对象", unknown: "未知" }, field.valueType, "table-select", { prop: "valueType" }));
    const sourceCell = document.createElement("td"); sourceCell.append(select(sourceLabels, field.source, "table-select", { prop: "source" }));
    const requiredCell = document.createElement("td"); const required = document.createElement("input"); required.type = "checkbox"; required.checked = field.required; required.dataset.prop = "required"; requiredCell.append(required);
    const ruleCell = document.createElement("td"); const rule = document.createElement("input"); rule.value = field.defaultRule || ""; rule.dataset.prop = "defaultRule"; rule.placeholder = field.source === "caller" ? "由用户填写" : "env:NAME / uuid / now:iso"; ruleCell.append(rule);
    row.append(pathCell, labelCell, typeCell, sourceCell, requiredCell, ruleCell); elements.fieldTableBody.append(row);
  }
  elements.bindingList.innerHTML = "";
  const approved = capability.bindings.filter(item => item.approved);
  if (!approved.length) elements.bindingList.textContent = "暂无已确认绑定；不会自动串联其他能力。";
  approved.forEach(binding => {
    const item = document.createElement("div"); item.className = "compact-item ok"; item.textContent = `${binding.fromCapabilityId}${binding.fromPath} → ${binding.toPath}`; elements.bindingList.append(item);
  });
  elements.validationList.innerHTML = "";
  if (!capability.validation.checks.length) elements.validationList.textContent = "保存或新识别后，需要重新运行验证。";
  capability.validation.checks.forEach(check => {
    const item = document.createElement("div"); item.className = `compact-item ${check.ok ? "ok" : "fail"}`; item.textContent = `${check.ok ? "✓" : "×"} ${check.detail}`; elements.validationList.append(item);
  });
}

function schemaPaths(schema, prefix = "$") {
  if (!schema || typeof schema !== "object") return [];
  const type = Array.isArray(schema.type) ? schema.type.find(item => item !== "null") : schema.type;
  if (type === "object" && schema.properties) {
    return Object.entries(schema.properties).flatMap(([name, child]) => schemaPaths(child, `${prefix}.${name}`));
  }
  if (type === "array" && schema.items) return schemaPaths(schema.items, `${prefix}[*]`);
  return prefix === "$" ? [] : [prefix];
}

function fillSelect(node, items, valueOf, labelOf) {
  node.innerHTML = "";
  for (const item of items) {
    const option = document.createElement("option"); option.value = valueOf(item); option.textContent = labelOf(item); node.append(option);
  }
}

function updateMappingPaths() {
  const source = state.catalog.capabilities.find(item => item.id === elements.mappingSource.value);
  const paths = schemaPaths(source?.outputSchema);
  fillSelect(elements.mappingValuePath, paths, item => item, item => item);
  fillSelect(elements.mappingLabelPath, paths, item => item, item => item);
}

function openMapping(mode) {
  const target = state.catalog.capabilities.find(item => item.id === state.selectedCapabilityId);
  if (!target) return;
  const sources = state.catalog.capabilities.filter(item => item.validation.status === "verified" && item.id !== target.id && (mode !== "candidate" || item.operation === "query"));
  const targetFields = target.inputForm.filter(field => mode !== "candidate" || field.source === "caller");
  if (!sources.length) return showToast(mode === "candidate" ? "没有可作为候选来源的已验证查询能力" : "没有可作为绑定来源的其他已验证能力");
  if (!targetFields.length) return showToast(mode === "candidate" ? "当前能力没有调用方选择字段" : "当前能力没有可绑定的输入字段");
  state.mappingMode = mode; elements.mappingTitle.textContent = mode === "candidate" ? "配置动态候选" : "确认数据绑定";
  elements.mappingDescription.textContent = mode === "candidate"
    ? "候选值和显示名称都必须来自已验证查询能力的真实返回结构。"
    : "只能选择已验证来源能力、真实返回字段和当前能力的已录制输入字段。";
  elements.mappingValueLabel.textContent = mode === "candidate" ? "候选稳定值字段" : "来源返回字段";
  elements.mappingLabelRow.hidden = mode !== "candidate"; elements.mappingNoteRow.hidden = mode === "candidate"; elements.mappingNote.value = "";
  fillSelect(elements.mappingSource, sources, item => item.id, item => `${item.title} · ${item.id}`);
  fillSelect(elements.mappingTarget, targetFields, item => item.path, item => `${item.label} · ${item.path}`);
  updateMappingPaths(); elements.mappingModal.hidden = false;
}

async function saveMapping(event) {
  event.preventDefault();
  const target = state.catalog.capabilities.find(item => item.id === state.selectedCapabilityId); if (!target) return;
  const source = state.catalog.capabilities.find(item => item.id === elements.mappingSource.value); if (!source) return;
  const mode = state.mappingMode;
  const summary = mode === "candidate"
    ? `${source.title} 的 ${elements.mappingValuePath.value} / ${elements.mappingLabelPath.value}\n→ ${target.title} 的 ${elements.mappingTarget.value}`
    : `${source.title} ${elements.mappingValuePath.value}\n→ ${target.title} ${elements.mappingTarget.value}`;
  elements.mappingModal.hidden = true;
  if (!(await confirmAction(mode === "candidate" ? "确认候选来源" : "确认自动数据绑定", `${summary}\n\n保存后能力会回到待验证，只有再次验证通过才可导出。`, false))) return;
  try {
    if (mode === "candidate") {
      await api("/api/candidates/configure", { method: "POST", body: JSON.stringify({
        targetCapabilityId: target.id, inputPath: elements.mappingTarget.value, sourceCapabilityId: source.id,
        valuePath: elements.mappingValuePath.value, labelPath: elements.mappingLabelPath.value, dependsOn: [], confirmed: true
      }) });
    } else {
      await api("/api/bindings/approve", { method: "POST", body: JSON.stringify({
        fromCapabilityId: source.id, fromPath: elements.mappingValuePath.value, toCapabilityId: target.id,
        toPath: elements.mappingTarget.value, note: elements.mappingNote.value.trim(), confirmed: true
      }) });
    }
    showToast(mode === "candidate" ? "动态候选已配置，请重新验证" : "数据绑定已确认，请重新验证"); await loadCatalog();
  } catch (error) { showToast(error.message); }
}

async function saveCapability(event) {
  event.preventDefault(); const capability = state.catalog.capabilities.find(item => item.id === state.selectedCapabilityId); if (!capability) return;
  const fields = [...elements.fieldTableBody.querySelectorAll("tr")].map(row => ({
    path: row.dataset.path, label: row.querySelector('[data-prop="label"]').value.trim(),
    valueType: row.querySelector('[data-prop="valueType"]').value, source: row.querySelector('[data-prop="source"]').value,
    required: row.querySelector('[data-prop="required"]').checked, defaultRule: row.querySelector('[data-prop="defaultRule"]').value.trim() || undefined
  }));
  try {
    await api(`/api/capabilities/${encodeURIComponent(capability.id)}`, { method: "PATCH", body: JSON.stringify({
      title: elements.capabilityTitle.value, operation: elements.capabilityOperation.value,
      description: elements.capabilityDescription.value, fields
    }) });
    showToast("能力已保存，请重新验证"); await loadCatalog();
  } catch (error) { showToast(error.message); }
}

async function runCatalogAction(kind) {
  const analyze = kind === "analyze";
  if (analyze && !(await confirmAction("重新识别能力", "将根据当前录制证据更新能力候选，并保留已人工修改的业务描述。是否继续？", false))) return;
  const button = analyze ? elements.analyzeCatalog : elements.validateCatalog; button.disabled = true;
  try {
    await api(analyze ? "/api/catalog/analyze" : "/api/catalog/validate", { method: "POST", body: JSON.stringify(analyze ? { useLlm: true } : {}) });
    showToast(analyze ? "能力识别完成" : "证据验证完成"); await loadCatalog();
  } catch (error) { showToast(error.message); } finally { button.disabled = false; }
}

async function loadSkills() {
  try { state.skills = (await api("/api/skills")).skills; renderSkills(); }
  catch (error) { showToast(error.message); }
}

function renderSkills() {
  elements.skillsList.innerHTML = "";
  if (!state.skills.length) {
    const empty = document.createElement("div"); empty.className = "empty-skills"; empty.innerHTML = "<strong>还没有导出的 Skill</strong><span>先在能力目录完成验证，再从上方导出。</span>"; elements.skillsList.append(empty); return;
  }
  for (const skill of state.skills) {
    const card = document.createElement("article"); card.className = "skill-card";
    const heading = document.createElement("div"); const title = document.createElement("div");
    const name = document.createElement("h2"); name.textContent = skill.displayName; const slug = document.createElement("code"); slug.textContent = skill.name; title.append(name, slug);
    const status = document.createElement("span"); status.className = `skill-status ${skill.status}`; status.textContent = skill.status === "frozen" ? "已冻结" : "可用"; heading.append(title, status);
    const facts = document.createElement("div"); facts.className = "skill-facts";
    [["版本", `v${skill.version}`], ["原子能力", skill.capabilityIds.length], ["组合路线", skill.routeIds.length], ["最近导出", timeLabel(skill.exportedAt)]].forEach(([label, value]) => {
      const fact = document.createElement("span"); fact.innerHTML = `<small>${label}</small><strong>${value}</strong>`; facts.append(fact);
    });
    const actions = document.createElement("div"); actions.className = "skill-actions";
    const actionData = [
      ["调用", "invoke", false], ["重新导出", "reexport", false], [skill.status === "frozen" ? "解除冻结" : "冻结", "freeze", false], ["删除", "delete", true]
    ];
    actionData.forEach(([label, action, danger]) => {
      const button = document.createElement("button"); button.type = "button"; button.textContent = label; button.className = danger ? "text-danger" : "";
      if (action === "reexport" && skill.status === "frozen") button.disabled = true;
      button.addEventListener("click", () => void skillAction(skill, action)); actions.append(button);
    });
    card.append(heading, facts, actions); elements.skillsList.append(card);
  }
}

async function exportSkill(name) {
  if (!(await confirmAction("导出 Python Skill", `将把当前全部已验证能力导出为“${name}”。未验证能力不会进入包。是否继续？`, false))) return;
  const result = await api("/api/skills/export", { method: "POST", body: JSON.stringify({ name, confirmed: true }) });
  showToast(`已导出 ${result.capabilityIds.length} 项能力，版本 v${result.version}`); await loadSkills();
}

async function skillAction(skill, action) {
  try {
    if (action === "invoke") {
      state.invokeSkill = skill; elements.invokeTitle.textContent = `调用 ${skill.displayName}`; elements.invokeGoal.value = ""; elements.invokeModal.hidden = false; elements.invokeGoal.focus(); return;
    }
    if (action === "reexport") { await exportSkill(skill.displayName); return; }
    if (action === "freeze") {
      const frozen = skill.status !== "frozen";
      if (!(await confirmAction(frozen ? "冻结 Skill" : "解除冻结", frozen ? "冻结后不能重新导出覆盖，但仍可调用当前版本。" : "解除后允许重新导出新版本。", false))) return;
      await api(`/api/skills/${encodeURIComponent(skill.name)}/freeze`, { method: "POST", body: JSON.stringify({ frozen, confirmed: true }) });
      showToast(frozen ? "Skill 已冻结" : "Skill 已解除冻结"); await loadSkills(); return;
    }
    if (action === "delete") {
      if (!(await confirmAction("删除 Skill", `将从目录移除“${skill.displayName}”。文件会移到项目回收区，可人工恢复。`, true))) return;
      await api(`/api/skills/${encodeURIComponent(skill.name)}/delete`, { method: "DELETE", body: JSON.stringify({ confirmed: true }) });
      showToast("Skill 已移到项目回收区"); await loadSkills();
    }
  } catch (error) { showToast(error.message); }
}

function connectEvents() {
  const stream = new EventSource("/api/events");
  stream.onmessage = message => {
    const event = JSON.parse(message.data);
    if (event.type === "agent_status") updateAgentStatus(event.ready, event.streaming);
    if (event.type === "assistant_start") beginAssistantMessage();
    if (event.type === "assistant_delta") appendAssistantDelta(event.delta || "");
    if (event.type === "assistant_done") finishAssistantMessage(event.message);
    if (event.type === "user_message") appendMessage(event.message);
    if (event.type === "tool_status") updateTool(event);
    if (event.type === "ui_request") showUiRequest(event);
    if (event.type === "browser_changed") void pollBrowser();
    if (event.type === "browser_mode") { state.browserMode = event.mode; renderBrowserMode(); }
    if (event.type === "runtime_log") appendRuntimeLog(event.entry);
    if (event.type === "catalog_changed" && state.view === "catalog") void loadCatalog();
    if (event.type === "skills_changed" && state.view === "skills") void loadSkills();
    if (event.type === "agent_error") showToast(event.message || "Pi 连接异常");
  };
  stream.onerror = () => updateAgentStatus(false, false);
}

document.querySelectorAll("[data-view]").forEach(button => button.addEventListener("click", () => setView(button.dataset.view)));
document.querySelectorAll("[data-browser-mode]").forEach(button => button.addEventListener("click", () => void changeBrowserMode(button.dataset.browserMode).catch(error => showToast(error.message))));
elements.addressForm.addEventListener("submit", event => { event.preventDefault(); void openBrowser(elements.browserUrl.value).catch(error => showToast(error.message)); });
elements.reloadBrowser.addEventListener("click", () => state.browserActive && void api("/api/browser/reload", { method: "POST", body: "{}" }).then(pollBrowser).catch(error => showToast(error.message)));
elements.recordingState.addEventListener("click", () => state.browserActive && void api("/api/browser/stop", { method: "POST", body: "{}" }).then(pollBrowser).catch(error => showToast(error.message)));
elements.composer.addEventListener("submit", event => { event.preventDefault(); void submitPrompt(elements.prompt.value); });
elements.prompt.addEventListener("keydown", event => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); elements.composer.requestSubmit(); } });
document.querySelectorAll("[data-prompt]").forEach(button => button.addEventListener("click", () => void submitPrompt(button.dataset.prompt || "")));
elements.confirmationCancel.addEventListener("click", () => void closeConfirmation(false)); elements.confirmationApprove.addEventListener("click", () => void closeConfirmation(true));
elements.capabilityForm.addEventListener("submit", saveCapability); elements.analyzeCatalog.addEventListener("click", () => void runCatalogAction("analyze"));
elements.validateCatalog.addEventListener("click", () => void runCatalogAction("validate"));
elements.addBinding.addEventListener("click", () => openMapping("binding")); elements.addCandidate.addEventListener("click", () => openMapping("candidate"));
elements.mappingSource.addEventListener("change", updateMappingPaths); elements.mappingForm.addEventListener("submit", event => void saveMapping(event));
elements.mappingCancel.addEventListener("click", () => { elements.mappingModal.hidden = true; state.mappingMode = null; });
elements.exportForm.addEventListener("submit", event => { event.preventDefault(); void exportSkill(elements.skillName.value.trim()).catch(error => showToast(error.message)); });
elements.invokeCancel.addEventListener("click", () => { elements.invokeModal.hidden = true; state.invokeSkill = null; });
elements.invokeSubmit.addEventListener("click", async () => {
  if (!state.invokeSkill || !elements.invokeGoal.value.trim()) return showToast("请先描述业务目标");
  try {
    await api(`/api/skills/${encodeURIComponent(state.invokeSkill.name)}/invoke`, { method: "POST", body: JSON.stringify({ goal: elements.invokeGoal.value }) });
    elements.invokeModal.hidden = true; state.invokeSkill = null; setView("recording"); showToast("已交给 Pi，执行中的选择和确认会显示在这里");
  } catch (error) { showToast(error.message); }
});
elements.browserFrame.addEventListener("click", event => {
  if (!state.browserActive || state.browserMode !== "manual") return;
  const point = browserCoordinates(event); if (!point) return;
  elements.browserViewport.focus();
  enqueueManualCommand({ action: "click", ...point });
});
elements.browserViewport.addEventListener("wheel", event => {
  if (!state.browserActive || state.browserMode !== "manual") return;
  event.preventDefault();
  const factor = event.deltaMode === WheelEvent.DOM_DELTA_LINE ? 40 : event.deltaMode === WheelEvent.DOM_DELTA_PAGE ? 600 : 1;
  enqueueManualCommand({ action: "scroll", deltaX: event.deltaX * factor, deltaY: event.deltaY * factor });
}, { passive: false });
elements.browserViewport.addEventListener("keydown", event => {
  if (!state.browserActive || state.browserMode !== "manual" || ["Control", "Shift", "Alt", "Meta"].includes(event.key)) return;
  event.preventDefault();
  const keyName = event.key === " " ? "Space" : event.key;
  const modifiers = [event.ctrlKey && "Control", event.altKey && "Alt", event.shiftKey && "Shift", event.metaKey && "Meta"].filter(Boolean);
  enqueueManualCommand({ action: "key", key: [...modifiers, keyName].join("+") });
});
elements.manualControls.addEventListener("submit", event => {
  event.preventDefault(); const value = elements.manualText.value; if (!value) return;
  elements.manualText.value = "";
  enqueueManualCommand({ action: "text", value });
});
document.querySelectorAll("[data-manual-key]").forEach(button => button.addEventListener("click", () => enqueueManualCommand({ action: "key", key: button.dataset.manualKey })));
elements.copyLogs.addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(state.runtimeLogs.map(entry => entry.line).join("\n"));
    showToast("运行日志已复制");
  } catch { showToast("浏览器未允许复制，请在日志面板中选择文字复制"); }
});

async function initialize() {
  try {
    const status = await api("/api/status"); updateAgentStatus(status.agent.ready, status.agent.streaming);
    elements.modelStatus.textContent = `${status.model || "由提供商选择模型"} · ${status.thinking}`;
    for (const message of status.messages || []) appendMessage(message);
    for (const entry of status.logs || []) appendRuntimeLog(entry);
    state.browserMode = status.browser?.mode || state.browserMode; renderBrowserMode(); await pollBrowser();
  } catch (error) { showToast(error.message); }
  connectEvents(); setInterval(pollBrowser, 900);
}

void initialize();
