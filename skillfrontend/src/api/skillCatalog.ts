export const SKILL_CATALOG_CHANGED = "dano:skill-catalog-changed";

export function skillDisplayId(skill: { name: string; action?: string }): string {
  return skill.action?.trim() || skill.name;
}

export function notifySkillCatalogChanged(target: EventTarget = window): void {
  target.dispatchEvent(new Event(SKILL_CATALOG_CHANGED));
}

export function observeSkillCatalogChanges(
  listener: EventListener,
  target: EventTarget = window,
): () => void {
  target.addEventListener(SKILL_CATALOG_CHANGED, listener);
  return () => target.removeEventListener(SKILL_CATALOG_CHANGED, listener);
}
