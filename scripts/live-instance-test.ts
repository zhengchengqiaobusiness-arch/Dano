import { cp, mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { loadConfig } from "../src/config.js";
import { BrowserRecorder } from "../src/browser/recorder.js";

function log(message: string, extra?: unknown) {
  const line = extra === undefined ? message : `${message} ${JSON.stringify(extra)}`;
  console.log(`[live] ${new Date().toISOString()} ${line}`);
}

async function slimProfile(source: string, dest: string) {
  await mkdir(dest, { recursive: true });
  const copies = [
    "Default/Network/Cookies",
    "Default/Network/Cookies-journal",
    "Default/Local Storage",
    "Default/Session Storage",
    "Default/Login Data",
    "Default/Login Data-journal",
    "Default/Preferences",
    "Local State"
  ];
  for (const rel of copies) {
    const from = path.join(source, rel);
    const to = path.join(dest, rel);
    await mkdir(path.dirname(to), { recursive: true });
    await cp(from, to, { recursive: true, force: true }).catch(() => {});
  }
}

const PAGES = [
  {
    name: "请假申请",
    url: "http://admin.dianshixinxi.com:90/oa/duty/leave",
    createHint: /^(发起请假|新增|创建|新建)$/
  },
  {
    name: "采购订单",
    url: "http://admin.dianshixinxi.com:90/erp/purchase/order",
    createHint: /^(新增|创建|新建|发起采购)$/
  }
];

function compactFields(fields: any[] = []) {
  return fields.map(field => ({
    label: field.label,
    name: field.name,
    kind: field.kind,
    filled: field.filled,
    skip: field.skip,
    disabled: field.disabled,
    required: field.required,
    value: String(field.value || "").slice(0, 80),
    selector: field.selector,
    rangeIndex: field.rangeIndex
  }));
}

function summarize(result: any) {
  return {
    ok: result?.ok,
    url: result?.url,
    title: result?.title,
    scope: result?.scope,
    todoCount: result?.todoCount,
    submitted: result?.submitted,
    sawRequest: result?.sawRequest,
    errors: result?.errors,
    failed: result?.failed,
    todoFields: compactFields(result?.todoFields),
    formFields: compactFields(result?.formFields)
  };
}

async function maybeLogin(recorder: BrowserRecorder, report: any) {
  const snap: any = await recorder.control({ action: "snapshot" });
  report.loginSnapshot = {
    url: snap.url,
    title: snap.title,
    labels: (snap.formFields || []).map((field: any) => field.label)
  };
  const labels = new Set((snap.formFields || []).map((field: any) => String(field.label || "")));
  const user = process.env.DIANSHI_USERNAME || process.env.BSS_USERNAME;
  const password = process.env.DIANSHI_PASSWORD || process.env.BSS_PASSWORD;
  const tenant = process.env.DIANSHI_TENANT_NAME || process.env.BSS_TENANT;
  const looksLogin = /login|auth|signin/i.test(String(snap.url || ""))
    || labels.has("用户名") || labels.has("密码") || labels.has("账号");
  if (!looksLogin) return { loggedIn: true, snap };
  if (!user || !password) return { loggedIn: false, snap, reason: "login-page-without-env" };
  if (tenant && (labels.has("租户") || labels.has("租户名"))) {
    await recorder.control({ action: "fill", selector: labels.has("租户名") ? "label=租户名" : "label=租户", value: tenant }).catch(() => {});
  }
  if (labels.has("用户名") || labels.has("账号")) {
    await recorder.control({ action: "fill", selector: labels.has("用户名") ? "label=用户名" : "label=账号", value: user });
  }
  if (labels.has("密码")) {
    await recorder.control({ action: "fill", selector: "label=密码", value: password });
  }
  const submit: any = await recorder.control({ action: "submit-form" }).catch(error => ({ ok: false, error: String(error) }));
  await recorder.control({ action: "wait", ms: 1200 });
  const after: any = await recorder.control({ action: "snapshot" });
  report.loginSubmit = { ok: submit?.ok, url: after.url, title: after.title };
  return { loggedIn: !/login|auth|signin/i.test(String(after.url || "")), snap: after };
}

async function exerciseAndSubmit(recorder: BrowserRecorder, tag: string, report: any) {
  log(`${tag} snapshot`);
  const before: any = await recorder.control({ action: "snapshot" });
  report[`${tag}Before`] = summarize(before);
  log(`${tag} fields`, compactFields(before.formFields || []).map(field => `${field.label}:${field.kind}:${field.filled ? "filled" : "todo"}`));
  log(`${tag} exercise-form`);
  let exercise: any = await recorder.control({ action: "exercise-form" });
  log(`${tag} exercise`, { ok: exercise?.ok, todo: exercise?.todoCount, failed: exercise?.failed });
  if ((exercise?.todoCount || 0) > 0) {
    log(`${tag} exercise-form retry`);
    const again: any = await recorder.control({ action: "exercise-form" });
    exercise = { ...again, firstFailed: exercise.failed, firstTodo: compactFields(exercise.todoFields) };
    log(`${tag} exercise retry`, { ok: again?.ok, todo: again?.todoCount, failed: again?.failed });
  }
  report[`${tag}Exercise`] = summarize(exercise);
  log(`${tag} submit-form`);
  const submit: any = await recorder.control({ action: "submit-form" }).catch(error => ({ ok: false, error: String(error?.message || error) }));
  log(`${tag} submit`, { ok: submit?.ok, submitted: submit?.submitted, sawRequest: submit?.sawRequest, errors: submit?.errors });
  report[`${tag}Submit`] = summarize(submit);
  const after: any = await recorder.control({ action: "snapshot" });
  report[`${tag}After`] = summarize(after);
  return { before, exercise, submit, after };
}

async function openCreate(recorder: BrowserRecorder, hint: RegExp, report: any) {
  const snap: any = await recorder.control({ action: "snapshot" });
  const button = (snap.controls || []).find((control: any) => {
    const text = String(control.text || "").replace(/\s+/g, "");
    const label = String(control.label || "").replace(/\s+/g, "");
    return hint.test(text) || hint.test(label) || /^发起/.test(text);
  });
  report.createButton = button ? { text: button.text, selector: button.selector, label: button.label } : null;
  report.createCandidates = (snap.controls || [])
    .filter((control: any) => /button|submit/i.test(String(control.tag || control.role || control.type || "")))
    .map((control: any) => ({ text: control.text, label: control.label, selector: control.selector }))
    .slice(0, 20);
  const clicks = button
    ? [
      /^(label|placeholder|text|role)=/i.test(String(button.selector || "")) ? button.selector : `text=${String(button.text || "").replace(/\s+/g, "")}`,
      button.text ? `text=${button.text}` : undefined
    ]
    : ["text=新增", "role=button[name=\"新增\"]", "text=发起请假", "role=button[name=\"发起请假\"]"];
  for (const selector of clicks.filter(Boolean)) {
    try {
      await recorder.control({ action: "click", selector: String(selector) });
      report.createButton = report.createButton || { selector, fallback: true };
      await recorder.control({ action: "wait", ms: 800 });
      break;
    } catch {
      /* try next */
    }
  }
  return recorder.control({ action: "snapshot" });
}

async function runPage(recorder: BrowserRecorder, page: typeof PAGES[number]) {
  const report: any = { name: page.name, url: page.url };
  log("goto", { url: page.url });
  await recorder.control({ action: "goto", url: page.url });
  await recorder.control({ action: "wait", ms: 800 });
  log("snapshot login check");
  const login = await maybeLogin(recorder, report);
  report.loggedIn = login.loggedIn;
  if (!login.loggedIn) return report;
  if (!new RegExp(page.url.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).test(String((login.snap as any)?.url || ""))) {
    await recorder.control({ action: "goto", url: page.url });
    await recorder.control({ action: "wait", ms: 800 });
  }
  await exerciseAndSubmit(recorder, "query", report);
  await openCreate(recorder, page.createHint, report);
  await exerciseAndSubmit(recorder, "create", report);
  return report;
}

async function main() {
  const base = loadConfig();
  const slim = path.join(base.dataDir, "live-profile");
  log("copy slim profile", { from: base.profileDir, to: slim });
  await slimProfile(base.profileDir, slim);
  const config = { ...base, headless: true, profileDir: slim };
  const recorder = new BrowserRecorder(config);
  const out: any = { startedAt: new Date().toISOString(), pages: [] as any[] };
  try {
    log("start recording", { url: PAGES[0]!.url });
    const session = await recorder.start(PAGES[0]!.url, "live-instance-both");
    out.sessionId = session.id;
    log("session started", { id: session.id });
    for (const page of PAGES) {
      try {
        log("run page", { name: page.name, url: page.url });
        const result = await runPage(recorder, page);
        out.pages.push(result);
        await writeFile(path.join(config.dataDir, "live-instance-report.json"), JSON.stringify(out, null, 2), "utf8");
        log("page done", {
          name: page.name,
          queryOk: result.queryExercise?.ok,
          queryTodo: result.queryExercise?.todoCount,
          createOk: result.createExercise?.ok,
          createTodo: result.createExercise?.todoCount
        });
      } catch (error: any) {
        log("page failed", { name: page.name, error: String(error?.message || error) });
        out.pages.push({ name: page.name, url: page.url, fatal: String(error?.stack || error) });
      }
    }
    out.session = await recorder.stop();
  } catch (error: any) {
    out.fatal = String(error?.stack || error);
    log("fatal", { error: out.fatal });
    if (recorder.isActive()) await recorder.stop().catch(() => {});
  }
  out.finishedAt = new Date().toISOString();
  const file = path.join(config.dataDir, "live-instance-report.json");
  await writeFile(file, JSON.stringify(out, null, 2), "utf8");
  console.log(JSON.stringify({
    sessionId: out.sessionId,
    fatal: out.fatal,
    report: file,
    pages: out.pages.map((page: any) => ({
      name: page.name,
      loggedIn: page.loggedIn,
      queryOk: page.queryExercise?.ok,
      queryTodo: page.queryExercise?.todoCount,
      queryFailed: page.queryExercise?.failed,
      querySubmit: page.querySubmit?.ok,
      createButton: page.createButton,
      createOk: page.createExercise?.ok,
      createTodo: page.createExercise?.todoCount,
      createFailed: page.createExercise?.failed,
      createSubmit: page.createSubmit?.ok,
      createErrors: page.createSubmit?.errors,
      fatal: page.fatal
    }))
  }, null, 2));
}

main().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
