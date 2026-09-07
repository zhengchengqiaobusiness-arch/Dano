/**
 * 把一项能力写入草稿。只合并调用方交来的对象，不推断字段。
 */

function asRecord(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

export function draftObject(raw) {
  const draft = asRecord(raw);
  return {
    ...draft,
    capabilities: Array.isArray(draft.capabilities) ? draft.capabilities.slice() : [],
    steps: Array.isArray(draft.steps) ? draft.steps.slice() : [],
    links: Array.isArray(draft.links) ? draft.links.slice() : [],
    unresolved: Array.isArray(draft.unresolved) ? draft.unresolved.slice() : [],
  };
}

export function mergeCapabilityIntoDraft(draft, {
  capability,
  steps = [],
  links = [],
  unresolved = [],
  title = "",
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

  const incomingSteps = Array.isArray(steps) ? steps : [];
  for (const step of incomingSteps) {
    const row = asRecord(step);
    const stepId = String(row.step_id || "").trim();
    if (!stepId) continue;
    const index = next.steps.findIndex((item) => String(item?.step_id) === stepId);
    if (index >= 0) next.steps[index] = row;
    else next.steps.push(row);
  }
  if (Array.isArray(links) && links.length) {
    next.links = next.links.concat(links);
  }
  if (Array.isArray(unresolved) && unresolved.length) {
    next.unresolved = next.unresolved.concat(unresolved);
  }
  if (title && !next.recording_goal && !next.title) {
    next.recording_goal = String(title);
  }
  return next;
}
