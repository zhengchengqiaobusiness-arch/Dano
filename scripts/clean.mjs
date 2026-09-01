import { rm } from "node:fs/promises";
await rm(".business-skill-studio", { recursive: true, force: true });
await rm("dist", { recursive: true, force: true });
console.log("Removed generated studio data.");
