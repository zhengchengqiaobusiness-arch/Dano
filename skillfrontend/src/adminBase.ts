/** Skill 管理后台的部署前缀。生产构建是 /admin/，本地开发是 /。 */

export function adminBasename(baseUrl = import.meta.env.BASE_URL): string {
  const trimmed = String(baseUrl || "/").replace(/\/+$/, "");
  return trimmed;
}

export function routerBasename(baseUrl = import.meta.env.BASE_URL): string | undefined {
  return adminBasename(baseUrl) || undefined;
}

export function adminHref(path: string, baseUrl = import.meta.env.BASE_URL): string {
  const suffix = path.startsWith("/") ? path : `/${path}`;
  const base = adminBasename(baseUrl);
  return base ? `${base}${suffix}` : suffix;
}
