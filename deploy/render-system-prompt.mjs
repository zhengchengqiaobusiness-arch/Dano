import { readFile } from "node:fs/promises";
import {
  resolveProductName,
  syncSystemPrompt,
} from "../apps/dano/runtime/system-prompt.mjs";

const [modeArgument, templatePath, targetPath] = process.argv.slice(2);
const mode =
  modeArgument === "--if-missing"
    ? "if-missing"
    : modeArgument === "--replace"
      ? "replace"
      : undefined;

if (!mode || !templatePath || !targetPath) {
  throw new Error(
    "usage: render-system-prompt.mjs <--if-missing|--replace> <template> <target>",
  );
}

async function resolveEffectiveProductName() {
  if (process.env.DANO_PRODUCT_NAME?.trim()) {
    return resolveProductName(process.env.DANO_PRODUCT_NAME, undefined);
  }

  const configPath =
    process.env.DANO_CONFIG_PATH?.trim() || "/app/dano.config.json";
  const config = JSON.parse(await readFile(configPath, "utf8"));
  return resolveProductName(
    process.env.DANO_PRODUCT_NAME,
    typeof config?.productName === "string" ? config.productName : undefined,
  );
}

try {
  const productName = await resolveEffectiveProductName();
  await syncSystemPrompt({ templatePath, targetPath, productName, mode });
} catch {
  console.error("[system-prompt] initialization failed; check product name and runtime paths");
  process.exitCode = 1;
}
