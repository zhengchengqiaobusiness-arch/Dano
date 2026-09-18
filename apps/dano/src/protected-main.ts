import { open } from "node:fs/promises";
import { constants } from "node:fs";
import { fileURLToPath } from "node:url";
import { rootFile } from "./bridge/trusted-installation.js";
import { parseProtectedSupervisorProfile } from "./bridge/protected-supervisor-profile.js";

/** Linux container entry. Pass only an administrator-owned JSON profile path. */
export async function runProtectedMain(args: readonly string[], environment: NodeJS.ProcessEnv = process.env): Promise<number> {
  if (process.platform !== "linux" || process.getuid?.() !== 0) throw new Error("PRIVILEGED_SUPERVISOR_REQUIRED");
  const [profilePath, ...hostArgs] = args;
  if (!profilePath) throw new Error("PROTECTED_SUPERVISOR_PROFILE_REQUIRED");
  const controller = new AbortController();
  const stop = () => controller.abort();
  process.on("SIGINT", stop); process.on("SIGTERM", stop);
  try {
    const canonical = await rootFile(profilePath);
    const file = await open(canonical, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
    let options;
    try {
      const metadata = await file.stat();
      if (!metadata.isFile() || metadata.uid !== 0 || (metadata.mode & 0o022) || metadata.size > 1024 * 1024) {
        throw new Error("INVALID_PROTECTED_SUPERVISOR_PROFILE");
      }
      const bytes = await file.readFile();
      if (bytes.length > 1024 * 1024) throw new Error("INVALID_PROTECTED_SUPERVISOR_PROFILE");
      options = parseProtectedSupervisorProfile(JSON.parse(bytes.toString("utf8")));
    } finally { await file.close(); }
    controller.signal.throwIfAborted();
    // Preserve the helper entry's module-relative paths in the production build.
    const { runProtectedSupervisor } = await import("./bridge/protected-supervisor.js");
    return await runProtectedSupervisor(options, environment, hostArgs, controller.signal);
  } finally { process.off("SIGINT", stop); process.off("SIGTERM", stop); }
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  runProtectedMain(process.argv.slice(2)).then(code => { process.exitCode = code; }, () => {
    console.error("[dano] Protected supervisor startup or shutdown failed.");
    process.exitCode = 1;
  });
}
