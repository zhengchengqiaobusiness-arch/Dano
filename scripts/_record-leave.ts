import { writeFile } from "node:fs/promises";
import path from "node:path";
import { loadConfig } from "../src/config.js";
import { StudioService } from "../src/studio-service.js";
import { isPrimaryCapability } from "../src/inference/export-scope.js";

const TARGET = "http://admin.dianshixinxi.com:90/oa/duty/leave";
const log = (label: string, value?: unknown) => {
  console.log(`\n=== ${label} ===`);
  if (value !== undefined) console.log(typeof value === "string" ? value : JSON.stringify(value, null, 2));
};

function compactSnapshot(snapshot: any) {
  return {
    url: snapshot?.url,
    title: snapshot?.title,
    scope: snapshot?.scope,
    todoCount: snapshot?.todoCount,
    errors: snapshot?.errors || [],
    formFields: (snapshot?.formFields || []).map((field: any) => ({
      label: field.label, name: field.name, kind: field.kind, filled: field.filled,
      skip: field.skip, required: field.required, value: field.value
    })),
    buttons: (snapshot?.controls || [])
      .filter((item: any) => item.tag === "button" || item.role === "button")
      .map((item: any) => ({ text: item.text || item.label, selector: item.selector }))
      .slice(0, 40)
  };
}

function controlText(item: any) {
  return String(item?.text || item?.label || "").replace(/\s+/g, "");
}

async function clickNamed(studio: StudioService, names: string[]) {
  const snapshot = await studio.recorder.control({ action: "snapshot" }) as any;
  const wanted = names.map(name => name.replace(/\s+/g, ""));
  const match = (snapshot.controls || []).find((item: any) => wanted.includes(controlText(item)));
  if (match?.selector) {
    await studio.recorder.control({ action: "click", selector: match.selector });
    return controlText(match);
  }
  for (const name of names) {
    try {
      await studio.recorder.control({ action: "click", selector: `text=${name}` });
      return name;
    } catch {
      // try next
    }
  }
  throw new Error(`未找到按钮：${names.join(" / ")}`);
}

async function maybeLogin(studio: StudioService, snapshot: any) {
  const url = String(snapshot?.url || "");
  const labels = (snapshot?.formFields || []).map((field: any) => String(field.label || ""));
  const loginLike = /login/i.test(url) || labels.some((label: string) => /用户名|密码|账号/.test(label));
  if (!loginLike) return false;
  const tenant = process.env.DIANSHI_TENANT_NAME;
  const username = process.env.DIANSHI_USERNAME;
  const password = process.env.DIANSHI_PASSWORD;
  if (!username || !password) throw new Error("当前是登录页，但环境变量缺少 DIANSHI_USERNAME / DIANSHI_PASSWORD");
  log("登录页，使用环境变量登录（不输出密码）", { tenant: Boolean(tenant), username: Boolean(username) });
  if (tenant && labels.some((label: string) => /租户/.test(label))) {
    await studio.recorder.control({ action: "fill", selector: "label=租户名称", value: tenant }).catch(async () => {
      await studio.recorder.control({ action: "fill", selector: "placeholder=请输入租户名称", value: tenant });
    });
  }
  await studio.recorder.control({ action: "fill", selector: "label=用户名", value: username }).catch(async () => {
    await studio.recorder.control({ action: "fill", selector: "placeholder=请输入用户名", value: username });
  });
  await studio.recorder.control({ action: "fill", selector: "label=密码", value: password }).catch(async () => {
    await studio.recorder.control({ action: "fill", selector: "placeholder=请输入密码", value: password });
  });
  await clickNamed(studio, ["登录", "登 录"]);
  await studio.recorder.control({ action: "wait", ms: 2000 });
  await studio.recorder.control({ action: "goto", url: TARGET });
  return true;
}

async function waitForForm(studio: StudioService, minFields = 1) {
  let snapshot: any;
  for (let index = 0; index < 20; index += 1) {
    snapshot = await studio.recorder.control({ action: "snapshot" });
    if ((snapshot?.formFields || []).length >= minFields) return snapshot;
    await studio.recorder.control({ action: "wait", ms: 800 });
  }
  return snapshot;
}

async function exerciseUntilDone(studio: StudioService, label: string) {
  let result: any;
  for (let index = 0; index < 4; index += 1) {
    result = await studio.recorder.control({ action: "exercise-form" });
    log(`${label} exercise-form #${index + 1}`, {
      ok: result.ok, scope: result.scope, todoCount: result.todoCount,
      filled: (result.filled || []).map((item: any) => item.label),
      failed: result.failed,
      leftover: (result.todoFields || []).map((item: any) => item.label),
      fields: (result.formFields || []).map((item: any) => `${item.label}=${item.value}`)
    });
    if (result.ok && (result.todoCount || 0) === 0) return result;
    await studio.recorder.control({ action: "wait", ms: 800 });
  }
  return result;
}

async function submitWithRepair(studio: StudioService, label: string) {
  let result: any = await studio.recorder.control({ action: "submit-form" }).catch(async (error: any) => {
    log(`${label} submit-form 不可用，改点提交`, String(error?.message || error));
    await clickNamed(studio, ["提交", "确定", "保 存", "保存", "搜索", "查询"]);
    await studio.recorder.control({ action: "wait", ms: 800 });
    return studio.recorder.control({ action: "snapshot" });
  });
  log(`${label} submit`, {
    ok: result.ok, submitted: result.submitted, repaired: result.repaired,
    errors: result.errors, url: result.url, scope: result.scope,
    leftover: (result.todoFields || []).map((item: any) => item.label)
  });
  if (result.errors?.length || (result.todoCount || 0) > 0) {
    await exerciseUntilDone(studio, `${label} 提交后补填`);
    result = await studio.recorder.control({ action: "submit-form" });
    log(`${label} 再次提交`, {
      ok: result.ok, errors: result.errors, url: result.url,
      leftover: (result.todoFields || []).map((item: any) => item.label)
    });
  }
  return result;
}

async function main() {
  const studio = new StudioService({ ...loadConfig(), headless: process.env.BSS_HEADLESS !== "false" });
  const session = await studio.startRecording(TARGET, "请假申请");
  log("录制已开始", { id: session.id, url: TARGET });
  try {
    let snapshot = await studio.recorder.control({ action: "snapshot" }) as any;
    await maybeLogin(studio, snapshot);
    snapshot = await waitForForm(studio, 4);
    const identified = compactSnapshot(snapshot);
    await writeFile(path.join(process.cwd(), ".business-skill-studio", "tmp-leave-identify.json"), JSON.stringify(identified, null, 2), "utf8");
    log("识别当前页面", identified);

    log("查询：填写全部筛选项");
    await exerciseUntilDone(studio, "查询");
    await studio.recorder.control({ action: "click", selector: "text=搜索" });
    await studio.recorder.control({ action: "wait", ms: 1500 });
    log("查询后页面", compactSnapshot(await studio.recorder.control({ action: "snapshot" }) as any));

    log("新建：打开新增");
    await clickNamed(studio, ["发起请假", "新增", "添 加", "添加"]);
    await studio.recorder.control({ action: "wait", ms: 1500 });
    log("新增表单", compactSnapshot(await waitForForm(studio, 4)));
    await exerciseUntilDone(studio, "新建");
    await studio.recorder.control({ action: "wait", ms: 800 });
    await exerciseUntilDone(studio, "新建补字段");
    await studio.recorder.control({ action: "click", selector: "text=保存草稿" });
    await studio.recorder.control({ action: "wait", ms: 2000 });
    const after = compactSnapshot(await studio.recorder.control({ action: "snapshot" }) as any);
    log("保存后页面", after);
  } finally {
    const stopped = await studio.stopRecording();
    log("录制已停止", { id: stopped.id });
    const analyzed = await studio.analyze(stopped.id, false);
    log("分析完成", analyzed.map(item => ({
      id: item.id, title: item.title, operation: item.operation, path: item.transport.pathTemplate
    })));
    const validated = await studio.validate();
    const verified = validated.filter(item => item.validation.status === "verified");
    log("验证完成", verified.map(item => ({
      id: item.id, title: item.title, operation: item.operation, path: item.transport.pathTemplate, primary: isPrimaryCapability(item, validated)
    })));
    const primary = verified.filter(item => isPrimaryCapability(item, validated));
    if (!primary.some(item => item.operation === "create")) {
      throw new Error("录制后仍没有已验证的新增能力");
    }
    const exported = await studio.export("请假申请");
    log("导出完成", exported);
    console.log("SESSION_ID=" + stopped.id);
  }
}

main().catch(error => {
  console.error(error);
  process.exit(1);
});
