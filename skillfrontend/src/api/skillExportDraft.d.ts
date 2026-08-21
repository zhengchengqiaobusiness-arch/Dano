declare module "./skillExportDraft.mjs" {
  export interface SkillExportDraftShape {
    title: string;
    description: string;
    planningMode: "dynamic" | "fixed";
    exampleRequests: string;
    successCriteria: string;
    forbiddenActions: string;
  }

  export interface RouteSummaryShape {
    name: string;
    whenToUse: string;
    steps: string[];
    autoCarry: string[];
    askWhen: string[];
    composition: string;
    needsConfirm: boolean;
  }

  export function normalizeSkillExportDraft(row: unknown): SkillExportDraftShape;
  export function serializeSkillExportDraft(draft: unknown): SkillExportDraftShape;
  export function routeSummaryFromOutcome(route: unknown): RouteSummaryShape;
}
