export function describeExportFailure(error: unknown): string[] {
  const err = error as {
    message?: string;
    config?: { url?: string; method?: string };
    response?: { status?: number; data?: unknown };
  };
  const lines: string[] = [];
  const method = String(err.config?.method || "POST").toUpperCase();
  const url = String(err.config?.url || "").trim();
  if (url) lines.push(`请求 ${method} ${url}`);
  if (err.response?.status) lines.push(`HTTP ${err.response.status}`);
  const data = err.response?.data;
  if (typeof data === "string" && data.trim()) lines.push(data.trim());
  else if (data && typeof data === "object") {
    const rec = data as Record<string, unknown>;
    if (typeof rec.detail === "string" && rec.detail.trim()) lines.push(rec.detail);
    if (typeof rec.error === "string" && rec.error.trim()) lines.push(rec.error);
    if (Array.isArray(rec.errors)) {
      for (const item of rec.errors) {
        if (item) lines.push(String(item));
      }
    }
    if (lines.length <= 2) lines.push(JSON.stringify(data));
  }
  if (!err.response && err.message) lines.push(err.message);
  if (!lines.length) lines.push("Skill 导出失败（没有响应体）");
  return lines;
}
