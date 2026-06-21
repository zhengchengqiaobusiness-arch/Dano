#!/usr/bin/env node
import { chmodSync, existsSync, readFileSync, writeFileSync } from "node:fs";

const args = process.argv.slice(2);
let envFile = ".env";
const positional = [];

for (let index = 0; index < args.length; index++) {
  const arg = args[index];
  if (arg === "--file") {
    envFile = args[++index] ?? "";
  } else {
    positional.push(arg);
  }
}

const name = positional[0];
if (!envFile || !name || !/^[A-Z][A-Z0-9_]*$/.test(name)) {
  console.error(
    "Usage: printf '%s' \"$SECRET\" | node scripts/set-env-secret.mjs NAME [--file .env]",
  );
  process.exit(1);
}

const secret = await new Promise((resolve, reject) => {
  let value = "";
  process.stdin.setEncoding("utf8");
  process.stdin.on("data", chunk => {
    value += chunk;
  });
  process.stdin.on("end", () => resolve(value.replace(/\r?\n$/, "")));
  process.stdin.on("error", reject);
});

if (!secret) {
  console.error(`[set-env-secret] no value provided for ${name}`);
  process.exit(1);
}

const lines = existsSync(envFile)
  ? readFileSync(envFile, "utf8").split(/\r?\n/)
  : [];
let updated = false;
const nextLines = lines.map(line => {
  if (line.startsWith(`${name}=`) || line.startsWith(`# ${name}=`)) {
    updated = true;
    return `${name}=${secret}`;
  }
  return line;
});

if (!updated) {
  if (nextLines.length > 0 && nextLines[nextLines.length - 1] !== "") {
    nextLines.push("");
  }
  nextLines.push(`${name}=${secret}`);
}

writeFileSync(envFile, `${nextLines.join("\n").replace(/\n*$/, "")}\n`, {
  mode: 0o600,
});
chmodSync(envFile, 0o600);
console.log(`[set-env-secret] updated ${name} in ${envFile}`);
