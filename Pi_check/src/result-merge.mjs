/**
 * 把一项能力写入草稿。只合并调用方交来的对象，不推断字段。
 * 同 id 再交：整项覆盖该能力；该项 step 上的 links、该项 from/to 的 relations 先删后加。
 * 省略 unresolved 保留上次；传入数组（含 []）才替换。
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

function capabilityStepIds(cap, incomingSteps) {
  const ids = new Set();
  for (const id of asList(cap?.step_ids)) {
    const text = String(id || "").trim();
    if (text) ids.add(text);
  }
  for (const ref of asList(cap?.request_refs)) {
    const text = String(ref?.step_id || "").trim();
    if (text) ids.add(text);
  }
  for (const step of incomingSteps) {
    const text = String(asRecord(step).step_id || "").trim();
    if (text) ids.add(text);
  }
  return ids;
}

function linkTouchesSteps(link, stepIds) {
  const row = asRecord(link);
  const source = String(row.source_step_id || "").trim();
  const target = String(row.target_step_id || "").trim();
  return (source && stepIds.has(source)) || (target && stepIds.has(target));
}

function relationTouchesCapability(rel, capabilityId) {
  const row = asRecord(rel);
  const from = String(row.from_capability || row.from || "").trim();
  const to = String(row.to_capability || row.to || "").trim();
  return from === capabilityId || to === capabilityId;
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

  const stepIds = capabilityStepIds(item, incomingSteps);
  next.links = next.links.filter((link) => !linkTouchesSteps(link, stepIds));
  const incomingLinks = asList(links);
  if (incomingLinks.length) {
    next.links = next.links.concat(incomingLinks.map((row) => asRecord(row)));
  }

  if (unresolved !== undefined && unresolved !== null) {
    next.unresolved = asList(unresolved);
  }

  next.capability_relations = (next.capability_relations || [])
    .filter((rel) => !relationTouchesCapability(rel, capabilityId));
  const incomingRelations = asList(capability_relations).map((row) => asRecord(row));
  if (incomingRelations.length) {
    next.capability_relations = [...next.capability_relations, ...incomingRelations];
  }

  const goal = String(recording_goal || "").trim();
  if (goal) next.recording_goal = goal;
  return next;
}
