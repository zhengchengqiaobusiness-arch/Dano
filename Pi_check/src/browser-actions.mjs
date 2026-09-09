/**
 * 页面操作原语：语义选择器、下拉一次选中、噪音请求过滤。
 * 只定位和点击，不推断能力。
 */

const NOISE_NETWORK = /getChatNotReadMessageCount|getChatNotRead|queryTopBarMessageCount|queryTodoTaskCount|queryNoticeCount|\/im\/chatMessage|\/common\/sysMessage\/queryTopBar|\/oa\/myTask\/queryTodoTaskCount|\/prod-api\/getInfo(?:\?|$)|\/prod-api\/getRouters(?:\?|$)|sockjs|websocket|favicon\.ico/i;

export const HUMAN_STEER_MS = 2500;

export function parseLocator(raw) {
  const text = String(raw || "").trim();
  if (!text) return { kind: "empty", value: "" };
  const refBare = text.match(/^(?:ref=)?([ca]\d+)$/i);
  if (refBare) return { kind: "ref", value: refBare[1] };
  const keyed = text.match(/^(placeholder|label|text|ref)=(.*)$/i);
  if (keyed) return { kind: keyed[1].toLowerCase(), value: keyed[2] };
  const typed = text.match(/^type=(checkbox|radio)$/i);
  if (typed) return { kind: "role", role: typed[1].toLowerCase(), name: "", value: typed[1].toLowerCase() };
  const role = text.match(/^role=([a-z0-9-]+)(?:\[name=["']?([^"'\]]+)["']?\])?$/i);
  if (role) return { kind: "role", role: role[1], name: role[2] || "", value: role[2] || role[1] };
  return { kind: "text", value: text };
}

export function normalizeVisibleLabel(value) {
  return String(value || "").replace(/\s+/g, "").trim();
}

export function advertisedSnapshotLabel(token, snapshot = null) {
  const parsed = parseLocator(token);
  const items = [
    ...(Array.isArray(snapshot?.controls) ? snapshot.controls : []),
    ...(Array.isArray(snapshot?.actions) ? snapshot.actions : []),
  ];
  const ref = parsed.kind === "ref" ? parsed.value : "";
  const hit = items.find((item) => (
    item.selector === token
    || item.ref === token
    || (ref && (item.ref === ref || `ref=${item.ref}` === token))
  ));
  return normalizeVisibleLabel(hit?.label || hit?.text || hit?.name || "");
}

export function expectedClickLabel(token, snapshot = null) {
  const parsed = parseLocator(token);
  if (parsed.kind === "role" && parsed.name) {
    return { expected: normalizeVisibleLabel(parsed.name), enforce: true };
  }
  if (parsed.kind === "text" && parsed.value) {
    return { expected: normalizeVisibleLabel(parsed.value), enforce: true };
  }
  if (parsed.kind === "ref") {
    const expected = advertisedSnapshotLabel(token, snapshot);
    return { expected, enforce: Boolean(expected) };
  }
  return { expected: "", enforce: false };
}

export function clickLabelMatches(expected, hit) {
  const want = normalizeVisibleLabel(expected);
  const got = normalizeVisibleLabel(hit);
  if (!want || !got) return false;
  return want === got;
}

export function snapshotSelector(item = {}, role = "control") {
  const placeholder = String(item.placeholder || "").trim();
  const label = String(item.label || item.text || "").trim();
  const kind = String(item.kind || item.control_kind || "");
  if (placeholder) return `placeholder=${placeholder}`;
  if (role === "action" && kind === "checkbox" && label) return `role=checkbox[name="${label}"]`;
  if (role === "action" && (kind === "row" || kind === "text") && label) return `text=${label}`;
  if (role === "action" && label) return `role=button[name="${label}"]`;
  if (label) return `label=${label}`;
  if (item.ref) return `ref=${item.ref}`;
  return "";
}

export function isNoiseNetworkPath(url) {
  return NOISE_NETWORK.test(String(url || ""));
}

export function resolveInteractionActor(handle, payload = {}) {
  const given = String(payload?.actor || "");
  if (given === "pi" || given === "human") return given;
  if (handle?.isPiActing?.()) return "pi";
  return "human";
}

export function shouldSteerHumanAct(kind, payload = {}) {
  if (kind !== "interaction") return false;
  if (String(payload?.actor || "") === "pi") return false;
  const action = String(payload?.kind || "");
  if (/pointer_move|mousemove|mouseover|mouseenter|input|change|fill/i.test(action)) return false;
  return /click|submit|pointer_up|pointer_down|choose/i.test(action);
}

export function isUsefulEvidenceThought(kind, payload = {}) {
  if (kind === "network_request") {
    const type = String(payload.resource_type || "");
    if (type && type !== "xhr" && type !== "fetch") return false;
    return !isNoiseNetworkPath(payload.path || payload.url || "");
  }
  if (kind === "visible_control") {
    return /page_ready|navigated/i.test(String(payload.reason || ""));
  }
  if (kind === "interaction") {
    const action = String(payload.kind || "");
    if (/pointer|mousemove|mouseover|mouseenter|input|change/i.test(action)) return false;
    return true;
  }
  if (kind === "page_navigated") return Boolean(payload.url || payload.frame_id);
  return false;
}

export function isUsefulAssistantThought(thought) {
  if (!thought || typeof thought !== "object") return false;
  if (thought.kind === "tool") return true;
  if (thought.kind === "user") return Boolean(String(thought.text || "").trim());
  const text = String(thought.text || "").trim();
  if (!text) return false;
  if (/^正在自动操作 \d+s/.test(text)) return false;
  if (/^开始新一轮模型分析/.test(text)) return false;
  if (/^模型user[：:]/.test(text)) return false;
  if (/^本轮结束 tools?=/.test(text)) return false;
  if (/^继续用 Control In App Browser/.test(text)) return false;
  if (/^继续用 control_in_app_browser/.test(text)) return false;
  if (isNoiseNetworkPath(text)) return false;
  if (/采集可见控件 \d+ 个/.test(text)) return false;
  return true;
}
