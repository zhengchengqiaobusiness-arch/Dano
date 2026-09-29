import fs from "node:fs";

const file = new URL("../src/agent/tools.mjs", import.meta.url);
const text = fs.readFileSync(file, "utf8");
const start = text.indexOf('{ name: "browser_act"');
const end = text.indexOf("\n", start);
if (start < 0 || end < 0) {
  console.error("browser_act line not found");
  process.exit(1);
}
const next = '  { name: "browser_act", description: "对最近一次快照里的 ref 做 open、click、fill、fill_fields、press、select、upload、screenshot。ref 整段照抄 fN:eN@数字。返回 clicked、requests、changed、snapshot、open。用这份快照，不要为同一次操作再 snapshot。requests 为空表示这一下没有 xhr/fetch。写字段前对要采用的 id 调用 network_get。", parameters: { type: "object", properties: { action: { type: "string" }, ref: { type: "string" }, text: { type: "string" }, key: { type: "string" }, url: { type: "string" }, file_path: { type: "string" }, fields: { type: "array" } }, required: ["action"] } },';
fs.writeFileSync(file, `${text.slice(0, start)}${next}${text.slice(end)}`);
console.log("ok");
