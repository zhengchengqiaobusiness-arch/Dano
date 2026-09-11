/** 出包、目录、token 只打 Pi_check，不走原始 back。 */

export function normalizePiCheckPath(pathname: string): string {
  const path = String(pathname || "").split("?")[0];
  if (path.startsWith("/v1/")) return path;
  if (
    path === "/skills"
    || path.startsWith("/skills/")
    || path === "/settings/token"
    || path === "/export/directory"
    || path.startsWith("/recording-results/")
  ) {
    return `/v1${path}`;
  }
  return path;
}

export function recordingResultsProxyTarget(
  pathname: string,
  _method = "GET",
): "piCheck" | "gateway" {
  const path = normalizePiCheckPath(pathname);
  if (path.includes("/export-skill") || /\/draft$/.test(path)) return "piCheck";
  return "gateway";
}

export function shouldRouteToPiCheck(pathname: string, method = "GET"): boolean {
  const path = normalizePiCheckPath(pathname);
  const verb = String(method || "GET").toUpperCase();
  if (path === "/v1/skills" || path.startsWith("/v1/skills/")) return true;
  if (path === "/v1/settings/token") return true;
  if (path === "/v1/export/directory" || path === "/export/directory") return true;
  if (path === "/v1/pi-recordings" || path.startsWith("/v1/pi-recordings/")) return true;
  if (path.startsWith("/v1/recording-results/")) {
    return recordingResultsProxyTarget(path, verb) === "piCheck";
  }
  if (path.includes("/export-skill")) return true;
  return false;
}
