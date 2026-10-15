import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, mkdir, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { declaredSubcommands } from "../src/skillpack/argparse-probe.mjs";
import { verifySkill } from "../src/skillpack/verify.mjs";
import { skillDir } from "../src/paths.mjs";

async function pythonAvailable() {
  for (const bin of ["python3", "python"]) {
    const ok = await new Promise((resolve) => {
      const child = spawn(bin, ["-c", "print(1)"]);
      child.on("error", () => resolve(false));
      child.on("close", (code) => resolve(code === 0));
    });
    if (ok) return true;
  }
  return false;
}

const skipNoPython = !(await pythonAvailable()) && "本机没有 python3/python，跳过 argparse 探测";

const CLIENT = [
  "import argparse",
  "def do_list():",
  "    return None",
  "def do_submit():",
  "    return None",
  "parser = argparse.ArgumentParser()",
  "sub = parser.add_subparsers(dest=\"command\")",
  "p_list = sub.add_parser(\"list\")",
  "p_list.set_defaults(func=do_list)",
  "p_submit = sub.add_parser(\"submit-item\")",
  "p_submit.add_argument(\"--title\")",
  "p_submit.add_argument(\"--confirm\", action=\"store_true\")",
  "p_submit.set_defaults(func=do_submit)",
  "parser.parse_args()",
  "",
].join("\n");

const SKILL = [
  "---",
  "name: sample",
  "description: sample",
  "---",
  "python3 scripts/client.py submit-item \\",
  "  --title x \\",
  "  --confirm",
  "python3 scripts/client.py list",
  "",
].join("\n");

async function pack(id) {
  process.env.CABP_DATA = await mkdtemp(path.join(os.tmpdir(), "cabp-argparse-"));
  const rec = { id, tenant: "", subsystem: "app", skillId: `app.${id}`, startUrl: "http://127.0.0.1/" };
  const dir = skillDir(rec.skillId);
  await mkdir(path.join(dir, "scripts"), { recursive: true });
  await mkdir(path.join(dir, "references"), { recursive: true });
  await mkdir(path.join(dir, "config"), { recursive: true });
  await writeFile(path.join(dir, "SKILL.md"), SKILL);
  await writeFile(path.join(dir, "scripts/client.py"), CLIENT);
  await writeFile(path.join(dir, "references/api.md"), "## 字段\n\n## 绑定\n\n## 未解决\n\n## 已验证读命令\n");
  await writeFile(path.join(dir, "config/runtime.json"), "{}\n");
  await writeFile(path.join(dir, "config/auth.local.json"), "{\"headers\":{}}\n");
  return { rec, dir };
}

test("declaredSubcommands reads argparse --confirm flags", { skip: skipNoPython }, async () => {
  const { dir } = await pack("rec_argparse_declared");
  assert.deepEqual(await declaredSubcommands(dir), { list: false, "submit-item": true });
});

test("continued write example is not command_not_run; unread list still is", { skip: skipNoPython }, async () => {
  const { rec, dir } = await pack("rec_argparse_verify");
  const result = await verifySkill(dir, rec.id, rec);
  assert.equal(result.errors.some((item) => item.code === "command_not_run" && String(item.command || "").includes("submit-item")), false);
  assert.ok(result.errors.some((item) => item.code === "command_not_run" && String(item.command || "").includes("list")));
});

test("syntax error returns null and verify falls back", { skip: skipNoPython }, async () => {
  process.env.CABP_DATA = await mkdtemp(path.join(os.tmpdir(), "cabp-argparse-bad-"));
  const rec = { id: "rec_argparse_syntax", tenant: "", subsystem: "app", skillId: "app.rec_argparse_syntax", startUrl: "http://127.0.0.1/" };
  const dir = skillDir(rec.skillId);
  await mkdir(path.join(dir, "scripts"), { recursive: true });
  await mkdir(path.join(dir, "references"), { recursive: true });
  await mkdir(path.join(dir, "config"), { recursive: true });
  await writeFile(path.join(dir, "SKILL.md"), "---\nname: sample\ndescription: sample\n---\npython scripts/client.py list\n");
  await writeFile(path.join(dir, "scripts/client.py"), "def (\n");
  await writeFile(path.join(dir, "references/api.md"), "## 字段\n\n## 绑定\n\n## 未解决\n\n## 已验证读命令\n");
  await writeFile(path.join(dir, "config/runtime.json"), "{}\n");
  await writeFile(path.join(dir, "config/auth.local.json"), "{\"headers\":{}}\n");
  assert.equal(await declaredSubcommands(dir), null);
  const result = await verifySkill(dir, rec.id, rec);
  assert.ok(Array.isArray(result.errors));
});
