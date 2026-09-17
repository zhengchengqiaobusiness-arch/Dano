#!/usr/bin/env node
import { execFileSync } from "node:child_process";
import { existsSync, lstatSync, readFileSync, realpathSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");

// Managed overlays contain exactly one literal value. Compose interpolation
// would make identity depend on secrets/.env; only escaped dollars are allowed.
export function readProductNameOverlay(path) {
  if (!existsSync(path)) return undefined;
  try {
    if (!lstatSync(path).isFile()) throw new Error();
    let value = JSON.parse(readFileSync(path, "utf8"));
    for (const key of ["services", "app", "environment", "DANO_PRODUCT_NAME"]) {
      if (!value || Array.isArray(value) || typeof value !== "object" ||
          Object.keys(value).length !== 1 || !Object.hasOwn(value, key)) throw new Error();
      value = value[key];
    }
    if (typeof value !== "string" || !value.trim() || value.replaceAll("$$", "").includes("$")) throw new Error();
    return value.replaceAll("$$", () => "$");
  } catch { throw new Error("PRODUCT_NAME_OVERLAY_INVALID"); }
}

export async function preflightProductIdentity(sourceDir, deployDir, targetSha) {
  try {
    const git = args => execFileSync("git", args, { cwd: sourceDir, encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] }).trim();
    const head = git(["rev-parse", "HEAD"]);
    if (!/^[a-f0-9]{40}$/.test(head) || (targetSha && head !== targetSha) ||
        git(["status", "--porcelain", "--untracked-files=no"])) throw new Error();
    // Load the target commit's runtime authority, with no dependency install.
    const { resolveProductName } = await import(pathToFileURL(join(sourceDir, "apps/dano/runtime/product-name.mjs")));
    const overlay = readProductNameOverlay(join(deployDir, "docker-compose.product-name.json"));
    const config = overlay === undefined ? JSON.parse(readFileSync(join(sourceDir, "dano.config.json"), "utf8")) : {};
    const productName = resolveProductName(overlay, typeof config?.productName === "string" ? config.productName : undefined);
    return { productName, source: overlay === undefined ? "dano.config.json" : "managed-overlay", targetSha: head };
  } catch {
    throw new Error("PRODUCT_IDENTITY_PREFLIGHT_FAILED: provide a valid productName in the target dano.config.json or repair the managed overlay using deploy-system-prompt.mjs set-name <name> on a healthy deployment; for a new deployment, configure the source productName. Resolve invalid overlay structure and commit/source mismatch before retrying. No configuration contents emitted.");
  }
}

if (process.argv[1] && realpathSync(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const [deployDir, targetSha, ...extra] = process.argv.slice(2);
    if (!deployDir || extra.length) throw new Error("Usage: deploy-product-identity.mjs <deploy-directory> [target-sha]");
    console.log(JSON.stringify(await preflightProductIdentity(root, resolve(deployDir), targetSha)));
  } catch (error) {
    console.error(error.message);
    process.exitCode = 1;
  }
}
