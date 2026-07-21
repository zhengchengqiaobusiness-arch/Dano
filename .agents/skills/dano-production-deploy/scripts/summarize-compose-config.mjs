#!/usr/bin/env node
import { createHash } from "node:crypto";
import {
  lstatSync,
  readFileSync,
  readdirSync,
  readlinkSync,
  realpathSync,
  statSync,
} from "node:fs";
import { basename, join, relative, resolve, sep } from "node:path";

let input = "";
for await (const chunk of process.stdin) input += chunk;

if (!input.trim()) {
  throw new Error("resolved Compose JSON input is empty");
}

const config = JSON.parse(input);
const configuredHashRoots = JSON.parse(
  process.env.DANO_NGINX_HASH_ROOTS_JSON || "[]",
);
if (!Array.isArray(configuredHashRoots)) {
  throw new Error("DANO_NGINX_HASH_ROOTS_JSON must be a JSON array");
}
const nginxHashRoots = configuredHashRoots.map(root => {
  const requested = resolve(String(root));
  const resolved = realpathSync(requested);
  if (requested !== resolved) {
    throw new Error(`nginx hash root cannot be a symlink: ${root}`);
  }
  if (!statSync(resolved).isDirectory()) {
    throw new Error(`nginx hash root must be a directory: ${root}`);
  }
  return resolved;
});
let rejectedHashSources = 0;

function keys(value) {
  if (Array.isArray(value)) return value.map(String).sort();
  return value && typeof value === "object" ? Object.keys(value).sort() : [];
}

function protectionState(environment) {
  const name = "HEIMDALL_PROTECT_CONFIG_OVERLAY";
  let value;
  if (Array.isArray(environment)) {
    const entry = environment.find(item => String(item).startsWith(`${name}=`));
    value = entry?.slice(name.length + 1);
  } else if (environment && typeof environment === "object") {
    value = environment[name];
  }
  if (value === undefined || value === null || value === "") return "absent";
  return String(value).trim() === "0" ? "disabled" : "enabled";
}

function safeImage(value) {
  if (typeof value !== "string") return null;
  return value.replace(/(^|\/\/)[^/@\s]+:[^/@\s]+@/g, "$1[credentials]@");
}

function safePort(port) {
  if (typeof port === "string" || typeof port === "number") return String(port);
  if (!port || typeof port !== "object") return null;
  return Object.fromEntries(
    ["host_ip", "published", "target", "protocol", "mode"]
      .filter(name => port[name] !== undefined)
      .map(name => [name, port[name]]),
  );
}

function hashConfigSource(source, target) {
  const hashesConfig =
    typeof source === "string" &&
    typeof target === "string" &&
    (target.startsWith("/etc/nginx/templates/") ||
      target === "/etc/nginx/dano" ||
      target.startsWith("/etc/nginx/dano/"));
  if (!hashesConfig) return undefined;

  try {
    const resolvedSource = realpathSync(source);
    const allowed = nginxHashRoots.some(
      root => resolvedSource === root || resolvedSource.startsWith(`${root}${sep}`),
    );
    if (!allowed) {
      rejectedHashSources += 1;
      return "rejected";
    }

    const hash = createHash("sha256");
    const root = resolvedSource;
    const walk = path => {
      const stat = lstatSync(path);
      const name = relative(root, path) || basename(path);
      if (stat.isSymbolicLink()) {
        hash.update(`link\0${name}\0${readlinkSync(path)}\0`);
        return;
      }
      if (stat.isDirectory()) {
        for (const entry of readdirSync(path).sort()) walk(join(path, entry));
        return;
      }
      if (stat.isFile()) {
        hash.update(`file\0${name}\0`);
        hash.update(readFileSync(path));
        hash.update("\0");
      }
    };
    walk(resolvedSource);
    return hash.digest("hex");
  } catch {
    return "unreadable";
  }
}

function safeMount(mount) {
  if (typeof mount === "string") {
    const parts = mount.split(":");
    const result = {
      source: parts[0] || null,
      target: parts[1] || null,
      readOnly: parts.slice(2).includes("ro"),
    };
    const contentSha256 = hashConfigSource(result.source, result.target);
    return contentSha256 ? { ...result, contentSha256 } : result;
  }
  if (!mount || typeof mount !== "object") return null;
  const result = {
    type: mount.type || null,
    source: mount.source || null,
    target: mount.target || null,
    readOnly: Boolean(mount.read_only),
  };
  const contentSha256 = hashConfigSource(result.source, result.target);
  return contentSha256 ? { ...result, contentSha256 } : result;
}

function safeResourceMount(resource) {
  if (typeof resource === "string") return { source: resource };
  if (!resource || typeof resource !== "object") return null;
  return Object.fromEntries(
    ["source", "target", "uid", "gid", "mode"]
      .filter(name => resource[name] !== undefined)
      .map(name => [name, resource[name]]),
  );
}

function resourceProjection(resources) {
  return Object.fromEntries(
    Object.entries(resources || {})
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([name, resource]) => [
        name,
        {
          external: Boolean(resource?.external),
          resolvedName: resource?.name || name,
        },
      ]),
  );
}

function dependsOnProjection(dependsOn) {
  if (Array.isArray(dependsOn)) {
    return Object.fromEntries(dependsOn.sort().map(name => [name, {}]));
  }
  return Object.fromEntries(
    Object.entries(dependsOn || {})
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([name, dependency]) => [
        name,
        dependency && typeof dependency === "object"
          ? Object.fromEntries(
              ["condition", "required", "restart"]
                .filter(key => dependency[key] !== undefined)
                .map(key => [key, dependency[key]]),
            )
          : {},
      ]),
  );
}

const services = Object.fromEntries(
  Object.entries(config.services || {})
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([name, service]) => [
      name,
      {
        image: safeImage(service.image),
        restart: service.restart || null,
        ports: (service.ports || []).map(safePort).filter(Boolean),
        expose: (service.expose || []).map(String).sort(),
        mounts: (service.volumes || []).map(safeMount).filter(Boolean),
        secrets: (service.secrets || []).map(safeResourceMount).filter(Boolean),
        configs: (service.configs || []).map(safeResourceMount).filter(Boolean),
        networks: keys(service.networks),
        dependsOn: dependsOnProjection(service.depends_on),
        capAdd: (service.cap_add || []).map(String).sort(),
        securityOpt: (service.security_opt || []).map(String).sort(),
        privileged: Boolean(service.privileged),
        readOnly: Boolean(service.read_only),
        user: service.user === undefined ? null : String(service.user),
        healthcheckConfigured: Boolean(service.healthcheck),
        heimdallProtection: protectionState(service.environment),
      },
    ]),
);

process.stdout.write(
  `${JSON.stringify(
    {
      name: config.name || null,
      services,
      networks: resourceProjection(config.networks),
      volumes: resourceProjection(config.volumes),
      secrets: resourceProjection(config.secrets),
      configs: resourceProjection(config.configs),
      rejectedHashSources,
    },
    null,
    2,
  )}\n`,
);

if (rejectedHashSources > 0) process.exitCode = 2;
