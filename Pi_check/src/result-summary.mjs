/**
 * PI 是唯一语义决策者；旧录制逻辑绝不启动。
 *
 * 历史列表只展示能力合同里的名字和请求数。
 * 不把录制目标文案当成 Skill 名，也不把整场证据条数当成请求数。
 */

const PLACEHOLDER_TITLES = new Set([
  "",
  "(未命名)",
  "(未捕获到业务请求)",
  "未命名录制",
  "录制业务",
  "录制业务流程",
]);

const INSTRUCTION_PREFIXES = [
  "请将我接下来",
  "请把我接下来",
  "请根据我接下来",
  "请将接下来",
  "请把接下来",
];

const INSTRUCTION_MARKERS = [
  "生成一个可调用能力",
  "分别生成一个",
  "每项业务操作",
  "接下来在页面中实际完成",
];

export function looksLikeRecordingGoal(text, goal = "") {
  const value = String(text || "").trim();
  if (!value || PLACEHOLDER_TITLES.has(value)) return true;
  const other = String(goal || "").trim();
  if (other && value === other) return true;
  if (INSTRUCTION_PREFIXES.some((prefix) => value.startsWith(prefix))) return true;
  return INSTRUCTION_MARKERS.some((marker) => value.includes(marker));
}

export function capabilityDisplayTitles(result) {
  const capabilities = Array.isArray(result?.capabilities) ? result.capabilities : [];
  const titles = [];
  const seen = new Set();
  for (const item of capabilities) {
    const label = String(item?.title || item?.name || "").trim();
    if (!label || looksLikeRecordingGoal(label) || seen.has(label)) continue;
    seen.add(label);
    titles.push(label);
  }
  return titles;
}

export function composeSkillTitle(titles) {
  const labels = (Array.isArray(titles) ? titles : [])
    .map((item) => String(item || "").trim())
    .filter(Boolean);
  if (!labels.length) return "";
  if (labels.length === 1) return labels[0];
  if (labels.length <= 3) return labels.join("、");
  return `${labels.slice(0, 3).join("、")}等`;
}

export function requestCountFromResult(result) {
  const seen = new Set();
  const add = (key) => {
    const value = String(key || "").trim();
    if (value) seen.add(value);
  };
  const steps = Array.isArray(result?.steps) ? result.steps : [];
  for (const [index, step] of steps.entries()) {
    add(
      step?.step_id
      || [step?.method || "", step?.path || step?.url || ""].join("|")
      || String(index),
    );
  }
  if (seen.size) return seen.size;
  for (const item of Array.isArray(result?.capabilities) ? result.capabilities : []) {
    for (const ref of Array.isArray(item?.request_refs) ? item.request_refs : []) {
      add(typeof ref === "object" ? (ref.step_id || ref.request_id) : ref);
    }
    for (const stepId of Array.isArray(item?.step_ids) ? item.step_ids : []) {
      add(stepId);
    }
  }
  if (seen.size) return seen.size;
  const requests = result?.request_facts?.requests;
  return Array.isArray(requests) ? requests.length : 0;
}

export function displayTitleFromResult({ userTitle = "", goal = "", result = null } = {}) {
  const chosen = String(userTitle || "").trim();
  if (chosen && !looksLikeRecordingGoal(chosen, goal)) return chosen;
  const composed = composeSkillTitle(capabilityDisplayTitles(result));
  if (composed) return composed;
  const specTitle = String(result?.title || "").trim();
  if (specTitle && !looksLikeRecordingGoal(specTitle, goal)) return specTitle;
  const understanding = result?.business_understanding && typeof result.business_understanding === "object"
    ? result.business_understanding
    : (result?.business && typeof result.business === "object" ? result.business : {});
  const businessName = String(understanding.business_name || understanding.object || "").trim();
  if (businessName && !looksLikeRecordingGoal(businessName, goal)) return businessName;
  return "未命名录制";
}
