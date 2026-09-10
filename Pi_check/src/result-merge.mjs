/**
 * 把一项能力写入草稿。只合并调用方交来的对象，不推断字段。
 */

function parseStructured(value) {
  if (typeof value !== "string") return value;
  const text = value.trim();
  if (!text || (text[0] !== "{" && text[0] !== "[")) return value;
  try {
    return JSON.parse(text);
  } catch {
    return value;
  }
}

function asRecord(value) {
  const parsed = parseStructured(value);
  return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {};
}

function asList(value) {
  const parsed = parseStructured(value);
  return Array.isArray(parsed) ? parsed : [];
}

export function draftObject(raw) {
  const draft = asRecord(raw);
  return {
    ...draft,
    capabilities: Array.isArray(draft.capabilities) ? draft.capabilities.slice() : [],
    steps: Array.isArray(draft.steps) ? draft.steps.slice() : [],
    links: Array.isArray(draft.links) ? draft.links.slice() : [],
    capability_relations: Array.isArray(draft.capability_relations) ? draft.capability_relations.slice() : [],
    unresolved: Array.isArray(draft.unresolved) ? draft.unresolved.slice() : [],
  };
}

export function mergeCapabilityIntoDraft(draft, {
  capability,
  steps = [],
  links = [],
  unresolved,
  capability_relations = [],
  title = "",
  recording_goal = "",
} = {}) {
  const next = draftObject(draft);
  const cap = asRecord(capability);
  const capabilityId = String(cap.capability_id || cap.id || "").trim();
  if (!capabilityId) {
    throw new Error("capability.capability_id 不能为空");
  }
  const item = { ...cap, capability_id: capabilityId };
  const capIndex = next.capabilities.findIndex((row) => String(row?.capability_id || row?.id) === capabilityId);
  if (capIndex >= 0) next.capabilities[capIndex] = item;
  else next.capabilities.push(item);

  const incomingSteps = asList(steps);
  for (const step of incomingSteps) {
    const row = asRecord(step);
    const stepId = String(row.step_id || "").trim();
    if (!stepId) continue;
    const index = next.steps.findIndex((item) => String(item?.step_id) === stepId);
    if (index >= 0) next.steps[index] = row;
    else next.steps.push(row);
  }
  const incomingLinks = asList(links);
  if (incomingLinks.length) {
    next.links = next.links.concat(incomingLinks);
  }
  if (Array.isArray(unresolved)) {
    next.unresolved = asList(unresolved);
  }
  const incomingRelations = asList(capability_relations).map((row) => asRecord(row));
  if (incomingRelations.length) {
    next.capability_relations = [...(next.capability_relations || []), ...incomingRelations];
  }
  const goal = String(recording_goal || "").trim();
  if (goal) next.recording_goal = goal;
  return next;
}
