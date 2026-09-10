export const SKILL_LIFECYCLE_LABELS: Record<string, { label: string; color: string }> = {
  ready_to_export: { label: "已有能力，待产出 Skill", color: "blue" },
  stage_six_done: { label: "已有能力，待产出 Skill", color: "blue" },
  verifying: { label: "已有能力，待产出 Skill", color: "blue" },
  verified_not_exported: { label: "已有能力，待产出 Skill", color: "blue" },
  generating: { label: "正在按最新能力导出 Skill", color: "processing" },
  exported: { label: "Skill 已导出", color: "success" },
  export_failed: { label: "Skill 导出失败", color: "error" },
  needs_reexport: { label: "能力已修改，Skill 需要重新产出", color: "warning" },
};

export function historyLifecycleView(item: {
  skill_lifecycle?: string;
  skill_export_status?: string;
  skill_needs_reexport?: boolean;
  published?: boolean;
}): { label: string; color: string } {
  const key = String(item.skill_lifecycle || "").trim();
  const exportStatus = String(item.skill_export_status || "").trim();
  if (key === "exported" || exportStatus === "exported") {
    return SKILL_LIFECYCLE_LABELS.exported;
  }
  if (key === "generating" || exportStatus === "generating") {
    return SKILL_LIFECYCLE_LABELS.generating;
  }
  if (key === "export_failed" || exportStatus === "export_failed") {
    return SKILL_LIFECYCLE_LABELS.export_failed;
  }
  if (key === "needs_reexport" || item.skill_needs_reexport) {
    return SKILL_LIFECYCLE_LABELS.needs_reexport;
  }
  if (SKILL_LIFECYCLE_LABELS[key]) return SKILL_LIFECYCLE_LABELS[key];
  return SKILL_LIFECYCLE_LABELS.ready_to_export;
}
