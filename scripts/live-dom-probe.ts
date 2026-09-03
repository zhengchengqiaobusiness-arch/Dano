import { cp, mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { chromium } from "playwright";
import { loadConfig } from "../src/config.js";

async function slimProfile(source: string, dest: string) {
  await mkdir(dest, { recursive: true });
  for (const rel of [
    "Default/Network/Cookies",
    "Default/Network/Cookies-journal",
    "Default/Local Storage",
    "Default/Session Storage",
    "Default/Login Data",
    "Default/Login Data-journal",
    "Default/Preferences",
    "Local State"
  ]) {
    const from = path.join(source, rel);
    const to = path.join(dest, rel);
    await mkdir(path.dirname(to), { recursive: true });
    await cp(from, to, { recursive: true, force: true }).catch(() => {});
  }
}

const DUMP = String.raw`
(() => {
  const clean = (value) => String(value || "").replace(/\s+/g, " ").trim().slice(0, 200);
  const vis = (el) => {
    if (!(el instanceof Element)) return false;
    const box = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    return box.width > 1 && box.height > 1 && style.display !== "none" && style.visibility !== "hidden" && !el.hidden;
  };
  const dialogs = [...document.querySelectorAll("[role='dialog'], [role='alertdialog'], .el-dialog, .el-drawer, .el-overlay-dialog, .el-overlay, .el-popover, .el-popper, .ant-modal")]
    .filter(vis)
    .map((el) => ({
      tag: el.tagName,
      cls: String(el.className || "").slice(0, 180),
      role: el.getAttribute("role"),
      title: clean((el.querySelector(".el-dialog__title, .ant-modal-title, .el-drawer__title, [class*='dialog__title'], [class*='modal-title']") || {}).textContent || ""),
      formItems: el.querySelectorAll(".el-form-item, .ant-form-item").length,
      rows: el.querySelectorAll("tbody tr, .el-table__row, .el-tree-node").length,
      buttons: [...el.querySelectorAll("button, [role='button']")].map((btn) => clean(btn.textContent)).filter(Boolean).slice(0, 8),
      text: clean(el.textContent).slice(0, 160)
    }));
  const placeholders = [...document.querySelectorAll("input, textarea")].filter(vis).map((el) => ({
    tag: el.tagName,
    ph: el.getAttribute("placeholder"),
    aria: el.getAttribute("aria-hidden"),
    readonly: el.readOnly,
    cls: String(el.className || "").slice(0, 80)
  }));
  const hits = [...document.querySelectorAll("*")].filter((el) => {
    const t = clean(el.textContent);
    return vis(el) && (t === "领导审批" || t === "人力审批" || t === "备注" || /^请输入备注/.test(el.getAttribute?.("placeholder") || ""));
  }).slice(0, 20).map((el) => {
    const host = el.closest("[class*='process'], [class*='workflow'], [class*='node'], [class*='user'], .el-form-item, [class*='card']") || el.parentElement;
    return {
      text: clean(el.textContent).slice(0, 40),
      tag: el.tagName,
      cls: String(el.className || "").slice(0, 120),
      host: host ? { tag: host.tagName, cls: String(host.className || "").slice(0, 160), html: host.outerHTML.slice(0, 700) } : null
    };
  });
  return {
    url: location.href,
    title: document.title,
    dialogs,
    placeholders,
    hits
  };
})()
`;

async function main() {
  const base = loadConfig();
  const slim = path.join(base.dataDir, "live-profile");
  await slimProfile(base.profileDir, slim);
  const context = await chromium.launchPersistentContext(slim, {
    headless: true,
    viewport: { width: 1440, height: 960 }
  });
  const page = context.pages()[0] || await context.newPage();
  const out: any = {};
  try {
    await page.goto("http://admin.dianshixinxi.com:90/oa/duty/leaveapply/create", { waitUntil: "domcontentloaded" });
    await page.locator(".el-timeline-item, .font-bold").filter({ hasText: "领导审批" }).first().waitFor({ timeout: 8_000 }).catch(() => {});
    await page.waitForTimeout(400);
    out.leaveBefore = await page.evaluate(DUMP);
    out.leaveTimeline = await page.evaluate(String.raw`(() => {
      const items = [...document.querySelectorAll(".el-timeline-item, [class*='timeline-item']")];
      return items.map((el) => ({
        text: String(el.textContent || "").replace(/\s+/g, " ").trim().slice(0, 80),
        html: el.outerHTML.slice(0, 1800),
        buttons: [...el.querySelectorAll("button, [role='button'], [class*='avatar'], [class*='plus'], img, svg, i")].map((node) => ({
          tag: node.tagName,
          cls: String(node.className || "").slice(0, 120),
          text: String(node.textContent || "").replace(/\s+/g, " ").trim().slice(0, 40),
          aria: node.getAttribute("aria-label")
        }))
      }));
    })()`);
    const hr = page.locator("#activity-task-Activity_0ag2wyz-2 button, .el-timeline-item").filter({ hasText: "人力审批" }).locator("button").first();
    out.hrButton = await hr.count();
    if (await hr.count()) {
      await hr.click({ timeout: 2000 });
      await page.waitForTimeout(1200);
      out.hrAfterClick = await page.evaluate(String.raw`(() => {
        const vis = (el) => {
          const box = el.getBoundingClientRect();
          const style = getComputedStyle(el);
          return box.width > 2 && box.height > 2 && style.display !== "none" && style.visibility !== "hidden";
        };
        return [...document.querySelectorAll("[role='dialog'], .el-dialog, .el-drawer, .el-overlay-dialog, .el-popper, .el-popover")]
          .filter(vis)
          .map((el) => ({
            cls: String(el.className || "").slice(0, 180),
            role: el.getAttribute("role"),
            title: String((el.querySelector(".el-dialog__title, .el-drawer__title, [class*='dialog__title'], [class*='title']") || {}).textContent || "").replace(/\s+/g, " ").trim().slice(0, 80),
            formItems: el.querySelectorAll(".el-form-item").length,
            rows: el.querySelectorAll("tbody tr, .el-table__row").length,
            tree: el.querySelectorAll(".el-tree-node").length,
            buttons: [...el.querySelectorAll("button")].map((btn) => JSON.stringify(btn.textContent)).slice(0, 10),
            text: String(el.textContent || "").replace(/\s+/g, " ").trim().slice(0, 220)
          }));
      })()`);
    }

    void 0;
  } finally {
    const file = path.join(base.dataDir, "live-dom-probe.json");
    await mkdir(base.dataDir, { recursive: true });
    await writeFile(file, JSON.stringify(out, null, 2), "utf8");
    console.log(JSON.stringify({
      file,
      leaveUrl: out.leaveBefore?.url,
      leaveDialogs: out.leaveBefore?.dialogs,
      leaveTimeline: (out.leaveTimeline || []).map((item: any) => ({ text: item.text, html: item.html, buttons: item.buttons })),
      hrButton: out.hrButton,
      hrAfterClick: out.hrAfterClick,
      afterLabel: out.leaveAfterLabelClick?.dialogs,
      afterPlus: out.leaveAfterPlusClick?.dialogs,
      purchaseUrl: out.purchaseBefore?.url,
      purchaseDialogs: out.purchaseBefore?.dialogs,
      purchaseRemark: { count: out.purchaseRemarkCount, visible: out.purchaseRemarkVisible },
      purchasePh: out.purchaseBefore?.placeholders,
      afterSupplier: out.purchaseAfterSupplier?.dialogs
    }, null, 2));
    await context.close();
  }
}

main().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
