/** 出包、目录、token 只打 Pi_check，不走原始 back。 */

export function normalizePiCheckPath(pathname: string): string {
  const path = String(pathname || "").split("?")[0];
  if (path.startsWith("/v1/")) return path;
  if (
    path === "/skills"
    || path.startsWith("/skills/")
    || path === "/settings/token"
    || path.startsWith("/recording-results/")
  ) {
    return `/v1${path}`;
  }
  return path;
}

export function shouldRouteToPiCheck(pathname: string, method = "GET"): boolean {
  const path = normalizePiCheckPath(pathname);
  const verb = String(method || "GET").toUpperCase();
  if (path === "/v1/skills" || path.startsWith("/v1/skills/")) return true;
  if (path === "/v1/settings/token") return true;
  if (path === "/v1/pi-recordings" || path.startsWith("/v1/pi-recordings/")) return true;
  if (path.includes("/export-skill")) return true;
  if ((verb === "PUT" || verb === "POST") && /^\/v1\/recording-results\/[^/]+\/draft$/.test(path)) return true;
  if (verb === "GET" && /^\/v1\/recording-results\/rec_[^/]+$/.test(path)) return true;
  return false;
}
