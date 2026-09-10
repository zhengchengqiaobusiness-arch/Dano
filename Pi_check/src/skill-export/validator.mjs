/**
 * 消费者包形状闸门。不认业务，不调 back。
 */

import { readFile, readdir, stat } from "node:fs/promises";
import path from "node:path";
import { contractFidelityIssues, handbookIsFaithful } from "./contract-materialize.mjs";

const REQUIRED_SECTIONS = ["立刻办理", "选择工作流", "执行协议", "按需读取资源", "鉴权"];
const PROCESS_LEAK = [
  "generator-guides", "阶段1", "阶段 1", "阶段6", "阶段7", "阶段8",
  "FlowSpec", "fingerprint", "x-dano-", "一页面对应一个 Skill",
  "每次只执行一项", "不得自行串联",
];
const CREDENTIAL_RE = [
  /(?:Bearer|Basic|Token)\s+(?![<{($])[A-Za-z0-9._~+/-]{12,}/i,
  /\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}/,
];

function issue(code, message, file, warning = false) {
  return { severity: warning ? "warning" : "error", code, path: file, message };
}

async function readText(file) {
  try {
    return await readFile(file, "utf8");
  } catch (error) {
    if (error?.code === "ENOENT") return null;
    throw error;
  }
}

async function readJson(file) {
  const text = await readText(file);
  if (text == null) return { missing: true, value: null };
  try {
    return { missing: false, value: JSON.parse(text) };
  } catch {
    return { missing: false, value: null, invalid: true };
  }
}

function leakCredentials(text, file, issues, { allow = false } = {}) {
  if (allow || !text) return;
  for (const pattern of CREDENTIAL_RE) {
    if (pattern.test(text)) {
      issues.push(issue("credential_leak", "handbook/合同禁止明文 token", file));
      return;
    }
  }
}

export async function validateSkillPackageDir(root, { sourceDraft = null } = {}) {
  const issues = [];
  const skillMd = path.join(root, "SKILL.md");
  const handbook = await readText(skillMd);
  if (handbook == null) {
    issues.push(issue("missing_file", "missing required file: SKILL.md", skillMd));
  } else {
    for (const section of REQUIRED_SECTIONS) {
      if (!handbook.includes(section)) {
        issues.push(issue("missing_section", `SKILL.md 缺少「${section}」`, skillMd));
      }
    }
    for (const marker of PROCESS_LEAK) {
      if (handbook.includes(marker)) {
        issues.push(issue("process_leak", `成品禁止出现: ${marker}`, skillMd));
      }
    }
    leakCredentials(handbook, skillMd, issues);
    const workflowBlock = handbook.split("选择工作流")[1] || "";
    const firstLine = workflowBlock.split(/\r?\n/).map((line) => line.trim()).find(Boolean) || "";
    if (firstLine && /只要|仅|单独|原子/.test(firstLine) && !/办理|流程|完整/.test(firstLine)) {
      issues.push(issue("default_route_first", "「选择工作流」第一行必须是默认完整办理", skillMd));
    }
  }

  const required = [
    "scripts/flow.py",
    "scripts/runtime.py",
    "scripts/client.py",
    "scripts/format_list.py",
    "references/CONTRACT.json",
    "references/INPUT_FORMS.md",
    "references/CAPABILITIES.md",
    "references/OPTIONS.md",
    "config/runtime.json",
    "config/auth.local.json",
  ];
  for (const rel of required) {
    if (await readText(path.join(root, rel)) == null) {
      issues.push(issue("missing_file", `missing required file: ${rel}`, path.join(root, rel)));
    }
  }

  const guides = path.join(root, "references", "generator-guides");
  try {
    if ((await stat(guides)).isDirectory()) {
      issues.push(issue("generator_guides", "成品禁止携带 references/generator-guides/", guides));
    }
  } catch {
    // 不存在才正确
  }

  try {
    const entries = await readdir(root, { withFileTypes: true });
    for (const entry of entries) {
      if (!entry.isDirectory() || ["scripts", "references", "config"].includes(entry.name)) continue;
      const nested = path.join(root, entry.name, "SKILL.md");
      if (await readText(nested) != null) {
        issues.push(issue("nested_package", `成品禁止套娃子包: ${entry.name}/SKILL.md`, nested));
      }
    }
  } catch {
    // 根目录不可读时由其它规则报
  }

  const contractFile = path.join(root, "references", "CONTRACT.json");
  const contract = await readJson(contractFile);
  if (contract.invalid) {
    issues.push(issue("invalid_json", "CONTRACT.json 不是合法 JSON", contractFile));
  }
  if (handbook && contract.value && !handbookIsFaithful(handbook, contract.value)) {
    issues.push(issue("handbook_fields", "SKILL.md 未覆盖全部能力字段或未写清可用默认值", skillMd));
  }
  const capabilities = Array.isArray(contract.value?.capabilities) ? contract.value.capabilities : [];
  const routes = Array.isArray(contract.value?.routes) ? contract.value.routes : [];
  leakCredentials(JSON.stringify(contract.value || {}), contractFile, issues);
  if (capabilities.length >= 2) {
    const multi = routes.find((route) => Array.isArray(route?.steps) && route.steps.length >= 2);
    const hasDefault = routes.some((route) => ["default", "handle"].some((key) => String(route?.route_id || route?.id || "").startsWith(key)) && Array.isArray(route?.steps) && route.steps.length >= 2);
    if (!multi && !hasDefault) {
      issues.push(issue("missing_default_route", "有 ≥2 个能力时必须有多步默认路线", contractFile));
    }
  }

  const forms = await readText(path.join(root, "references", "INPUT_FORMS.md")) || "";
  leakCredentials(forms, path.join(root, "references", "INPUT_FORMS.md"), issues);
  if (/删除\s*dataSource|删掉\s*dataSource|再删\s*dataSource/.test(forms)) {
    issues.push(issue("dropped_datasource", "动态字段禁止删掉 dataSource", path.join(root, "references", "INPUT_FORMS.md")));
  }
  const clientSrc = await readText(path.join(root, "scripts", "client.py")) || "";
  if (/def request\s*\(/.test(clientSrc)) {
    issues.push(issue("patched_client", "冻结 client 禁止 request() 别名，只认 http_json(query=, body=)", path.join(root, "scripts", "client.py")));
  }
  const flowSrc = await readText(path.join(root, "scripts", "flow.py")) || "";
  if (flowSrc && !/import runtime/.test(flowSrc)) {
    issues.push(issue("invented_flow", "flow.py 必须调用冻结 runtime，禁止自编执行器", path.join(root, "scripts", "flow.py")));
  }
  if (/client\.request\s*\(/.test(flowSrc)) {
    issues.push(issue("patched_flow", "flow.py 禁止调用不存在的 client.request", path.join(root, "scripts", "flow.py")));
  }
  if (sourceDraft && !contract.missing && contract.value) {
    for (const message of contractFidelityIssues(contract.value, sourceDraft)) {
      issues.push(issue("contract_fidelity", message, contractFile));
    }
  }
  const steps = Array.isArray(contract.value?.steps) ? contract.value.steps : [];
  const optionSteps = steps.filter((step) => String(step?.usage || "") === "option_source" || /option/i.test(String(step?.name || "")));
  const refs = capabilities.flatMap((cap) => Array.isArray(cap.request_refs) ? cap.request_refs : []);
  const hasOptionRef = refs.some((ref) => String(ref?.usage || "") === "option_source") || optionSteps.length;
  if (hasOptionRef && forms && !/dataSource/.test(forms)) {
    issues.push(issue("dropped_datasource", "合同有 option_source 时 INPUT_FORMS 必须保留 dataSource", path.join(root, "references", "INPUT_FORMS.md")));
  }

  const authFile = path.join(root, "config", "auth.local.json");
  const auth = await readJson(authFile);
  if (!auth.missing && auth.invalid) {
    issues.push(issue("invalid_json", "auth.local.json 不是合法 JSON", authFile));
  } else if (!auth.missing && (!auth.value || typeof auth.value.headers !== "object" || Array.isArray(auth.value.headers))) {
    issues.push(issue("auth_shape", "auth.local.json 必须是 {headers:{}}", authFile));
  }

  const walk = async (dir, rel = "") => {
    let entries = [];
    try {
      entries = await readdir(dir, { withFileTypes: true });
    } catch {
      return;
    }
    for (const entry of entries) {
      const nextRel = rel ? `${rel}/${entry.name}` : entry.name;
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        if (entry.name === ".isolated") continue;
        await walk(full, nextRel);
        continue;
      }
      if (nextRel.replace(/\\/g, "/") === "config/auth.local.json") continue;
      if (!/\.(md|json|py)$/i.test(entry.name)) continue;
      const text = await readText(full);
      leakCredentials(text, full, issues);
    }
  };
  await walk(root);

  const errors = issues.filter((item) => item.severity === "error");
  return { ok: errors.length === 0, issues };
}
