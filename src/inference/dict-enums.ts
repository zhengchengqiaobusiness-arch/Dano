import type { CapabilityContract, EvidenceEvent, InputFormField } from "../domain.js";
import { recordedLists } from "./field-resolver.js";

function rowDisplay(row: Record<string, unknown>) {
  const value = row.name ?? row.label ?? row.title ?? row.nickname;
  return value === undefined || value === null || value === "" ? "" : String(value);
}

function rowIdentity(row: Record<string, unknown>) {
  return row.value ?? row.id ?? row.code;
}

function namesOf(list: ReturnType<typeof recordedLists>[number]) {
  return new Set(list.rows.map(rowDisplay).filter(Boolean));
}

function bestListForLabels(labels: string[], lists: ReturnType<typeof recordedLists>) {
  const scored = lists
    .map(list => ({ list, overlap: labels.filter(label => namesOf(list).has(label)).length }))
    .filter(item => item.overlap >= 2)
    .sort((left, right) => right.overlap - left.overlap);
  if (!scored.length) return undefined;
  const top = scored.filter(item => item.overlap === scored[0]!.overlap);
  if (top.length > 1) {
    const agree = labels.every(label => {
      const identities = new Set(top.map(item => {
        const row = item.list.rows.find(entry => rowDisplay(entry) === label);
        return row ? String(rowIdentity(row) ?? "") : "";
      }).filter(Boolean));
      return identities.size <= 1;
    });
    if (!agree) return undefined;
  }
  return top[0]!.list;
}

function applyRecordedList(field: InputFormField, lists: ReturnType<typeof recordedLists>) {
  if (field.candidates?.type !== "static") return field;
  const labels = field.candidates.values.map(item => String(item.label));
  if (labels.length < 2) return field;
  const best = bestListForLabels(labels, lists);
  if (!best) return field;
  return {
    ...field,
    widget: "select" as const,
    candidates: {
      type: "static" as const,
      values: labels.map(label => {
        const row = best.rows.find(item => rowDisplay(item) === label);
        const previous = field.candidates?.type === "static"
          ? field.candidates.values.find(item => String(item.label) === label)?.value
          : undefined;
        return { label, value: row ? rowIdentity(row) ?? previous ?? label : previous ?? label };
      })
    }
  };
}

export function attachDictEnums(catalog: CapabilityContract[], events: EvidenceEvent[]) {
  const lists = recordedLists(events.filter(event => event.kind === "network"));
  if (!lists.length) return catalog;
  return catalog.map(capability => ({
    ...capability,
    inputForm: capability.inputForm.map(field => applyRecordedList(field, lists))
  }));
}
