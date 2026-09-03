import type { Frame, Locator, Page } from "playwright";
import { MARK_LABELED_CONTROL, SNAPSHOT_IN_PAGE } from "./page-script.js";

export const FORM_ITEMS = ".el-form-item, .ant-form-item, .arco-form-item, .n-form-item, .van-field, [class*='form-item']";
export const FORM_LABELS = "label, .el-form-item__label, .ant-form-item-label, .arco-form-item-label, .n-form-item-label, .van-field__label";
export const DIALOGS = "[role='dialog']:visible, [role='alertdialog']:visible, .el-dialog:visible, .el-drawer:visible, .ant-modal:visible, .ant-drawer-content:visible, .arco-modal:visible, .arco-drawer:visible";
export const DROPDOWNS = ".el-select-dropdown:visible, .el-cascader__dropdown:visible, .el-autocomplete-suggestion:visible, .ant-select-dropdown:visible, .arco-select-dropdown:visible, [role='listbox']:visible";
export const DATE_PANELS = ".el-picker-panel:visible, .el-popper.el-date-picker:visible, .el-picker__popper:visible, .ant-picker-dropdown:visible, .arco-picker-container:visible, [class*='picker-dropdown']:visible, [class*='picker-panel']:visible";
export const OPTION_ITEMS = "[role='option'], [role='treeitem'], .el-select-dropdown__item, .el-cascader-node, .el-autocomplete-suggestion__list li, .ant-select-item-option, .arco-select-option, .n-base-select-option";
export const DIALOG_CHOICES = "[role='option'], [role='treeitem'], [role='listitem'], [role='row'], tbody tr, .el-table__body .el-table__row, .el-tree-node__content, .el-cascader-node, [role='radio'], .el-checkbox";
export const WIDGET_SURFACES = "xpath=ancestor-or-self::*[contains(@class,'el-select__wrapper') or contains(@class,'el-input__wrapper') or contains(@class,'el-date-editor') or contains(@class,'ant-select-selector') or contains(@class,'ant-picker') or contains(@class,'arco-select-view') or contains(@class,'arco-picker') or contains(@class,'picker-range') or contains(@class,'date-editor')][1]";
export const BUSY_SPINNERS = ".el-loading-mask:visible, .el-overlay.is-loading:visible, .nprogress-busy:visible, .ant-spin-spinning:visible, .arco-spin-loading:visible, [aria-busy='true']:visible";

export interface FormField {
  label: string;
  name?: string;
  selector: string;
  kind: string;
  filled: boolean;
  skip: boolean;
  disabled: boolean;
  required?: boolean;
  invalid?: boolean;
  value?: string;
  scope?: string;
  rangeIndex?: number;
  groupIndex?: number;
}

export interface PageSnapshot {
  title?: string;
  url?: string;
  text?: string;
  scope?: string;
  controls?: Array<Record<string, unknown>>;
  formFields?: FormField[];
  todoFields?: FormField[];
  todoCount?: number;
  errors?: string[];
  frames?: unknown[];
  recentUserActions?: unknown[];
}

export interface PageActionHost {
  page(): Page;
  writePageInventory(page: Page, snapshot: PageSnapshot): Promise<void>;
  recentUserActions(): unknown[];
  recordSelectObservation?(info: {
    label?: string;
    name?: string;
    scope?: "page" | "dialog";
    value?: string;
    options: Array<{ value: unknown; label: string }>;
  }): Promise<void>;
}

function escapeRegExp(value: string) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function exactText(value: string) {
  return new RegExp(`^\\s*${escapeRegExp(value)}\\s*$`);
}

function pad2(value: number) {
  return String(value).padStart(2, "0");
}

function localIsoDate(offsetDays = 0) {
  const date = new Date();
  date.setDate(date.getDate() + offsetDays);
  return `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())}`;
}

function parseFieldDate(value?: string) {
  const match = String(value || "").trim().match(/(\d{4}-\d{2}-\d{2})(?:\s+(\d{2}:\d{2}(?::\d{2})?))?/);
  if (!match) return undefined;
  const time = match[2] ? (match[2].length === 5 ? `${match[2]}:00` : match[2]) : "00:00:00";
  const date = new Date(`${match[1]}T${time}`);
  return Number.isNaN(date.getTime()) ? undefined : date;
}

function isNumericZero(value?: string) {
  return /^(0+|0*\.0+)$/.test(String(value || "").trim());
}

const SUBMIT_LABEL = /^(提交|确定|保存|搜索|查询|search|submit|save|ok|confirm|apply)/i;
const CANCEL_LABEL = /取消|关闭|重置|reset|cancel|close|back/i;

export class PageActions {
  constructor(private readonly host: PageActionHost) {}

  private page() {
    return this.host.page();
  }

  async waitForPageQuiet(timeout = 600) {
    const busy = this.page().locator(BUSY_SPINNERS);
    if (!(await busy.count())) return;
    await busy.first().waitFor({ state: "hidden", timeout }).catch(() => {});
  }

  private async awaitFormRequest(timeout = 2_500, write = false) {
    try {
      await this.page().waitForResponse(response => {
        const request = response.request();
        const type = request.resourceType();
        if (type !== "xhr" && type !== "fetch") return false;
        if (write) return /^(POST|PUT|PATCH|DELETE)$/i.test(request.method());
        return true;
      }, { timeout });
      return true;
    } catch {
      return false;
    }
  }

  async hasDatePanel() {
    return (await this.page().locator(DATE_PANELS).count()) > 0;
  }

  async hasDialog() {
    return Boolean(await this.lastFormDialog(this.page()));
  }

  private async isFormDialog(item: Locator) {
    return item.evaluate(el => !/picker-panel|picker-dropdown|datepicker|date-picker/i.test(el.className || "")).catch(() => true);
  }

  private async lastFormDialog(root: Frame | Locator | Page) {
    const all = root.locator(DIALOGS);
    const count = await all.count();
    for (let index = count - 1; index >= 0; index -= 1) {
      const item = all.nth(index);
      if (await this.isFormDialog(item)) return item;
    }
    return undefined;
  }

  private async formDialogCount() {
    const all = this.page().locator(DIALOGS);
    const count = await all.count();
    let total = 0;
    for (let index = 0; index < count; index += 1) {
      if (await this.isFormDialog(all.nth(index))) total += 1;
    }
    return total;
  }

  locatorIn(root: Frame | Locator, selector: string) {
    const placeholder = selector.match(/^placeholder=(.+)$/s);
    if (placeholder) return root.getByPlaceholder(placeholder[1]!);
    const label = selector.match(/^label=(.+)$/s);
    if (label) {
      const name = label[1]!;
      return root.getByLabel(name, { exact: true })
        .or(root.getByRole("combobox", { name, exact: true }))
        .or(root.getByRole("textbox", { name, exact: true }))
        .or(root.getByPlaceholder(name, { exact: true }));
    }
    const text = selector.match(/^text=(.+)$/s);
    if (text) return root.getByText(text[1]!, { exact: true });
    const role = selector.match(/^role=([a-z]+)(?:\[name=["'](.+)["']\])?$/i);
    if (role) return root.getByRole(role[1] as "button", role[2] ? { name: role[2] } : {});
    return root.locator(selector);
  }

  private scopeRoot(root: Frame | Locator) {
    return typeof (root as Frame).url === "function" ? (root as Frame).locator("body") : root as Locator;
  }

  private async labeledControl(root: Frame | Locator, name: string) {
    const mark = `bss-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
    const scope = this.scopeRoot(root);
    await scope.evaluate(() => {
      for (const el of document.querySelectorAll("[data-bss-locate]")) el.removeAttribute("data-bss-locate");
    }).catch(() => {});
    const ok = await scope.evaluate(MARK_LABELED_CONTROL, { name, mark }).catch(() => false);
    if (!ok) return undefined;
    const found = scope.locator(`[data-bss-locate="${mark}"]`).first();
    if (!(await found.count())) return undefined;
    if (await found.isVisible().catch(() => false)) return found;
    const host = found.locator("xpath=ancestor-or-self::*[contains(@class,'select') or contains(@class,'picker') or contains(@class,'cascader') or @role='combobox'][1]");
    if (await host.count() && await host.first().isVisible().catch(() => false)) return host.first();
    return undefined;
  }

  isDayTextSelector(selector: string) {
    const text = selector.match(/^text=(.+)$/s)?.[1]?.trim();
    return Boolean(text && /^\d{1,2}$/.test(text));
  }

  isDateCellSelector(selector: string) {
    return /el-date-picker|el-date-table|el-picker-panel|ant-picker|available\.today|date-table-cell/.test(selector)
      || this.isDayTextSelector(selector);
  }

  isFieldSelector(selector: string) {
    return /^(label|placeholder)=/i.test(selector);
  }

  async locate(selector: string) {
    const page = this.page();
    const deadline = Date.now() + 1_500;
    const dayOnly = this.isDayTextSelector(selector);
    const fieldOnly = this.isFieldSelector(selector);
    const textOnly = /^text=/.test(selector) && !dayOnly;
    while (Date.now() < deadline) {
      for (const frame of page.frames()) {
        if (dayOnly) {
          const panel = frame.locator(DATE_PANELS).last();
          if (await panel.count()) {
            const day = selector.match(/(\d{1,2})/)?.[1] || "";
            const cell = panel.locator(".el-date-table-cell__text, .el-date-table-cell, td.available .cell, td.available, .ant-picker-cell-inner")
              .filter({ hasText: exactText(String(Number(day))) }).last();
            if (await cell.count()) return cell;
          }
          continue;
        }
        const dialog = await this.lastFormDialog(frame);
        const dropdown = frame.locator(DROPDOWNS).last();
        const scopes: Array<Frame | Locator> = [];
        if (textOnly && await dropdown.count()) scopes.push(dropdown);
        if (dialog) scopes.push(dialog);
        if (!fieldOnly || !dialog) scopes.push(frame);
        for (const scope of scopes) {
          if (fieldOnly && /^label=/i.test(selector)) {
            const name = selector.replace(/^label=/i, "");
            const table = await this.tableControl(scope, name);
            if (table) return table;
            const labeled = await this.labeledControl(scope, name);
            if (labeled) return labeled;
            for (const candidate of [
              this.scopeRoot(scope).getByLabel(name, { exact: true }),
              this.scopeRoot(scope).getByRole("combobox", { name, exact: true }),
              this.scopeRoot(scope).getByRole("textbox", { name, exact: true }),
              this.scopeRoot(scope).getByRole("button", { name, exact: true }),
              this.scopeRoot(scope).getByPlaceholder(name, { exact: true })
            ]) {
              const unique = candidate.filter({ visible: true });
              if (await unique.count() === 1) return unique.first();
            }
            continue;
          }
          const found = this.locatorIn(scope, selector).filter({ visible: true });
          if (await found.count() === 1) {
            const item = found.first();
            if (textOnly && await this.isNavigationTarget(item)) continue;
            return item;
          }
          if (await found.count() > 1 && !fieldOnly) {
            const item = found.first();
            if (textOnly && await this.isNavigationTarget(item)) continue;
            return item;
          }
          if (fieldOnly) {
            const name = selector.replace(/^(label|placeholder)=/i, "");
            const table = await this.tableControl(scope, name);
            if (table) return table;
          }
        }
      }
      await page.waitForTimeout(40);
    }
    if (dayOnly) throw new Error("Refusing to click a bare day number on the page; open the date field first");
    throw new Error(`Selector not found in the page or its frames: ${selector}`);
  }

  async click(selector: string) {
    if (this.isDayTextSelector(selector)) {
      const day = selector.match(/(\d{1,2})/)?.[1] || (selector.includes("today") ? String(new Date().getDate()) : "");
      await this.pickCalendarDay(day || String(new Date().getDate()));
      return { ok: true, url: this.page().url() };
    }
    const intent = this.isFieldSelector(selector) ? "field" : "button";
    await this.clickSafely(await this.clickTarget(selector), intent);
    return { ok: true, url: this.page().url() };
  }

  async tableControl(root: Frame | Locator, name: string) {
    const tables = root.locator(".el-table, .ant-table, table");
    const tableCount = await tables.count();
    for (let tableIndex = 0; tableIndex < tableCount; tableIndex += 1) {
      const table = tables.nth(tableIndex);
      const headers = table.locator(".el-table__header th, .el-table__header-wrapper th, thead th, .ant-table-thead th, .el-table__header .el-table__cell");
      const headerCount = await headers.count();
      for (let index = 0; index < headerCount; index += 1) {
        const text = ((await headers.nth(index).innerText().catch(() => "")) || "").replace(/\s+/g, " ").trim();
        const norm = (value: string) => value.replace(/[*：:\s]/g, "");
        if (text !== name && norm(text) !== norm(name)) continue;
        const cell = table.locator(".el-table__body tr, .el-table__body .el-table__row, tbody tr").first()
          .locator("td, .el-table__cell, .ant-table-cell").nth(index);
        const input = cell.locator("input, textarea, select, [role='combobox'], .el-select__wrapper").filter({ visible: true }).first();
        if (await input.count()) return input;
      }
    }
    return undefined;
  }

  async isNavigationTarget(locator: Locator) {
    return locator.first().evaluate(el => {
      const inPicker = el.closest(".el-select-dropdown,.ant-select-dropdown,.el-picker-panel,.el-cascader__dropdown,[role='listbox'],[role='dialog'],.el-dialog,.ant-modal");
      if (inPicker) return false;
      if (el.closest("[class*='process'], [class*='workflow'], [class*='user-select'], [class*='add-user']")) return false;
      return Boolean(el.closest("nav, .el-menu, .ant-menu, .el-menu-item, .ant-menu-item, .el-pagination, .ant-pagination"));
    }).catch(() => false);
  }

  async clickTarget(selector: string) {
    const locator = await this.locate(selector);
    const mark = `bss-click-${Date.now().toString(36)}`;
    const marked = await locator.evaluate((el, token) => {
      const start = el.matches("input, textarea") ? (el.parentElement || el) : el;
      let best = start;
      let node: Element | null = start;
      for (let i = 0; i < 6 && node && node !== document.body; i += 1, node = node.parentElement) {
        const box = node.getBoundingClientRect();
        if (box.height > 72 || box.width > 720) break;
        if (node !== start && node.matches("[class*='form-item'], [class*='form-field'], [role='group'], td, th, [class*='table-cell'], [class*='table__cell']")) break;
        const cls = String(node.className || "");
        const chooser = (node.getAttribute("role") === "combobox" && !node.matches("input, textarea"))
          || Boolean(node.getAttribute("aria-haspopup"))
          || node.hasAttribute("aria-expanded")
          || (/(?:^|\s)(el-select|ant-select|arco-select|n-select|el-cascader|el-date-editor|ant-picker|arco-picker)(?:\s|$)/.test(cls))
          || (/(?:^|\s|_|-)(select|picker|cascader|date-editor)(?:\s|$|_)/i.test(cls) && !/dropdown|panel|item|option/i.test(cls));
        best = node;
        if (chooser) break;
      }
      best.setAttribute("data-bss-click", token);
      return true;
    }, mark).catch(() => false);
    if (marked) {
      const surfaces = [
        this.page().locator(`[data-bss-click="${mark}"]`),
        locator.locator(`xpath=ancestor-or-self::*[@data-bss-click="${mark}"][1]`)
      ];
      for (const surface of surfaces) {
        const visible = surface.filter({ visible: true });
        if (await visible.count()) return visible.first();
      }
    }
    const parent = locator.locator("xpath=ancestor::*[not(self::input or self::textarea)][1]");
    if (await parent.count()) return parent.first();
    return locator;
  }

  async clickSafely(locator: Locator, intent: "field" | "option" | "button" = "button") {
    const kind = await locator.first().evaluate((el, clickIntent) => {
      const box = el.getBoundingClientRect();
      const overlayChrome = ".el-dialog,.el-drawer,.el-picker-panel,.el-select-dropdown,.el-popper,.el-date-picker,.el-select,.el-date-editor,.ant-modal,.ant-select-dropdown,.arco-modal,[role='dialog'],[role='listbox'],[role='option']";
      const inOverlayChrome = el.closest(overlayChrome);
      if (el.matches(".el-overlay,.el-overlay-dialog,.v-modal,.ant-modal-mask,.arco-modal-mask") && !el.matches("[role='dialog'],.el-dialog,.ant-modal,.arco-modal")) return "mask";
      const mask = el.closest(".el-overlay,.el-overlay-dialog,.v-modal,.ant-modal-mask,.arco-modal-mask");
      if (mask === el) return "mask";
      if (mask && !inOverlayChrome) return "mask";
      const nav = el.closest("nav, .el-menu, .ant-menu, .el-menu-item, .ant-menu-item, .el-pagination, .ant-pagination");
      const inPicker = el.closest(".el-select-dropdown,.ant-select-dropdown,.el-picker-panel,[role='listbox'],.el-dialog,[role='dialog']");
      if (nav && !inPicker) return "nav";
      if (el.closest("a[href]") && !inPicker && clickIntent !== "button") return "nav";
      if (el.closest(".el-picker-panel,.el-select-dropdown,.el-cascader__dropdown,.el-popper,.ant-picker-dropdown,.ant-select-dropdown,[role='option']")) return "ok";
      const top = document.elementFromPoint(box.left + box.width / 2, box.top + box.height / 2);
      if (top && top !== el && !el.contains(top) && !top.contains(el)) {
        const coveredByMask = top.closest(".el-overlay,.el-overlay-dialog,.ant-modal-mask")
          && !top.closest(".el-dialog,.el-drawer,.el-picker-panel,.el-select-dropdown,.el-popper,.ant-modal,[role='option']");
        if (coveredByMask) return "occluded";
      }
      return "ok";
    }, intent).catch(() => "ok");
    if (kind === "mask") throw new Error("Refusing to click the modal mask; that would close the dialog");
    if (kind === "occluded") throw new Error("Target is behind an open dialog; click a control inside the dialog or picker");
    if (kind === "nav") throw new Error("Refusing to click navigation; that would leave the form and discard filled fields");
    try {
      await locator.first().click({ timeout: 1_200 });
    } catch {
      const allowForce = await locator.first().evaluate((el, clickIntent) => {
        const box = el.getBoundingClientRect();
        if (box.width * box.height > 80_000) return false;
        if (el.matches(".el-overlay,.el-overlay-dialog,.v-modal,.ant-modal-mask,.arco-modal-mask,a[href]")) return false;
        if (el.closest("nav, .el-menu, .ant-menu") && !el.closest(".el-select-dropdown,[role='listbox'],.el-dialog,[role='dialog'],[class*='process'],[class*='workflow']")) return false;
        return clickIntent === "field" || clickIntent === "option"
          || el.matches("button, [role='button'], input, textarea, select, [type='submit'], [type='button']");
      }, intent).catch(() => intent !== "button");
      if (!allowForce) throw new Error("Click failed; not forcing a page click that can navigate away");
      await locator.first().click({ force: true, timeout: 800 });
    }
  }

  private async dropdownScope() {
    const page = this.page();
    const dropdown = page.locator(DROPDOWNS).last();
    if (await dropdown.count()) return dropdown;
    const loose = page.locator("ul:visible, [role='listbox']:visible").filter({
      has: page.locator(OPTION_ITEMS)
    }).last();
    if (!(await loose.count())) return undefined;
    if (await this.isNavigationTarget(loose)) return undefined;
    return loose;
  }

  optionLocator(label: string, scope?: Locator) {
    const exact = exactText(label);
    const root = scope || this.page();
    return root.getByRole("option", { name: label, exact: true })
      .or(root.locator(OPTION_ITEMS).filter({ hasText: exact }));
  }

  async pickCalendarDay(dayText: string) {
    if (!(await this.hasDatePanel())) throw new Error("Refusing to click a bare day number on the page; open the date field first");
    const page = this.page();
    const panel = page.locator(DATE_PANELS).last();
    const day = String(Number(dayText));
    const cell = panel.getByRole("gridcell", { name: day, exact: true })
      .or(panel.locator("[role='gridcell'], .el-date-table-cell__text, .el-date-table-cell, td.available .cell, td.available, .ant-picker-cell-inner")
        .filter({ hasText: exactText(day) }))
      .last();
    await this.clickSafely(cell, "option");
  }

  private async clickActiveFormLabel() {
    const dialog = await this.lastFormDialog(this.page());
    const root = dialog || this.page().locator("body");
    const label = root.locator(FORM_LABELS).filter({ visible: true }).first();
    if (await label.count()) await label.click({ timeout: 400 }).catch(() => {});
  }

  async dismissTransientOverlays() {
    const page = this.page();
    try {
      if (await this.hasDatePanel() || await this.dropdownScope()) {
        await page.keyboard.press("Tab").catch(() => {});
        await page.locator(`${DATE_PANELS}, ${DROPDOWNS}`).first().waitFor({ state: "hidden", timeout: 400 }).catch(() => {});
      }
      if (await this.hasDatePanel()) {
        await this.clickActiveFormLabel();
        await page.locator(DATE_PANELS).first().waitFor({ state: "hidden", timeout: 400 }).catch(() => {});
      }
      if (await this.dropdownScope()) {
        const opener = page.locator("[aria-expanded='true'], .el-select__wrapper.is-focused, .el-select.is-focused .el-select__wrapper, .ant-select-open .ant-select-selector").first();
        if (await opener.count()) await this.clickSafely(opener, "field").catch(() => {});
        else await this.clickActiveFormLabel();
        await page.locator(DROPDOWNS).first().waitFor({ state: "hidden", timeout: 500 }).catch(() => {});
      }
    } catch {
      await page.locator(`${DATE_PANELS}, ${DROPDOWNS}`).first().waitFor({ state: "hidden", timeout: 200 }).catch(() => {});
    }
  }

  async waitForDropdown(timeout = 1_600) {
    const deadline = Date.now() + timeout;
    while (Date.now() < deadline) {
      const scope = await this.dropdownScope();
      if (scope) return scope;
      await this.page().waitForTimeout(40);
    }
    return undefined;
  }

  private async overlayState() {
    return this.page().evaluate(`(() => {
      const vis = (sel) => [...document.querySelectorAll(sel)].filter((el) => {
        const box = el.getBoundingClientRect();
        const style = getComputedStyle(el);
        return box.width > 2 && box.height > 2 && style.display !== "none" && style.visibility !== "hidden";
      });
      const keyOf = (el) => {
        const box = el.getBoundingClientRect();
        return [el.className, Math.round(box.top), Math.round(box.left), String(el.textContent || "").replace(/\\s+/g, " ").trim().slice(0, 48)].join("|");
      };
      const dialogs = vis("[role='dialog'], [role='alertdialog'], .el-dialog, .el-drawer, .ant-modal, .arco-modal")
        .filter((el) => !/picker-panel|picker-dropdown|datepicker|date-picker/i.test(String(el.className || "")));
      const drops = vis(".el-select-dropdown, .el-cascader__dropdown, .el-autocomplete-suggestion, .ant-select-dropdown, .arco-select-dropdown, [role='listbox']");
      const dates = vis(".el-picker-panel, .el-popper.el-date-picker, .el-picker__popper, .ant-picker-dropdown, .arco-picker-container, [class*='picker-panel'], [class*='picker-dropdown']");
      return {
        dialogs: dialogs.length,
        drops: drops.map(keyOf).sort().join("||"),
        dates: dates.map(keyOf).sort().join("||")
      };
    })()`);
  }

  private overlayOpened(before: { dialogs: number; drops: string; dates: string }, after: { dialogs: number; drops: string; dates: string }) {
    return after.dialogs > before.dialogs || after.drops !== before.drops || after.dates !== before.dates;
  }

  private async chooserOpened(before: { dialogs: number; drops: string; dates: string }) {
    await this.waitForPageQuiet(300);
    return this.overlayOpened(before, await this.overlayState());
  }

  private async activateChooser(selector: string) {
    await this.dismissTransientOverlays();
    const before = await this.overlayState();
    const host = await this.clickTarget(selector);
    const raw = await this.locate(selector);
    const inner = raw.locator("input, textarea, [role='combobox'], [aria-haspopup]").first();
    const surfaces = [host];
    if (await inner.count()) surfaces.push(inner);
    surfaces.push(raw);
    for (const surface of surfaces) {
      await this.clickSafely(surface, "field").catch(() => {});
      if (await this.chooserOpened(before)) return { before, opened: true };
      await surface.evaluate(el => { if (el instanceof HTMLElement) el.click(); }).catch(() => {});
      if (await this.chooserOpened(before)) return { before, opened: true };
      await surface.locator("input, [role='combobox']").first().press("ArrowDown").catch(async () => {
        await surface.press("ArrowDown").catch(() => {});
      });
      if (await this.chooserOpened(before)) return { before, opened: true };
    }
    const group = raw.locator("xpath=ancestor::*[self::div or self::li or self::section or self::td][1]");
    const affordances = group.locator("button, [role='button'], [aria-haspopup]").filter({ visible: true });
    const extra = Math.min(await affordances.count(), 3);
    for (let index = 0; index < extra; index += 1) {
      await this.clickSafely(affordances.nth(index), "field").catch(() => {});
      if (await this.chooserOpened(before)) return { before, opened: true };
    }
    return { before, opened: await this.chooserOpened(before) };
  }

  async openSelect(target: Locator) {
    await this.dismissTransientOverlays();
    const before = await this.overlayState();
    const inner = target.locator("input, textarea, [role='combobox']").first();
    const surfaces = [target];
    if (await inner.count()) surfaces.push(inner);
    for (const surface of surfaces) {
      await this.clickSafely(surface, "field").catch(() => {});
      if (await this.chooserOpened(before)) {
        const scope = await this.dropdownScope();
        if (scope) return scope;
      }
      await surface.evaluate(el => { if (el instanceof HTMLElement) el.click(); }).catch(() => {});
      if (await this.chooserOpened(before)) {
        const scope = await this.dropdownScope();
        if (scope) return scope;
      }
      await surface.press("ArrowDown").catch(async () => {
        await surface.locator("input").first().press("ArrowDown").catch(() => {});
      });
      if (await this.chooserOpened(before)) {
        const scope = await this.dropdownScope();
        if (scope) return scope;
      }
    }
    const scope = await this.waitForDropdown(400);
    if (!scope) throw new Error("Select dropdown did not open");
    if (before.drops && !this.overlayOpened(before, await this.overlayState())) {
      throw new Error("Select dropdown did not open");
    }
    return scope;
  }

  private async dateEditorMode(dateWrap: Locator) {
    return dateWrap.evaluate(el => {
      const blob = `${el.className} ${el.querySelector("input")?.placeholder || ""}`;
      return /datetime|datetimerange|time|时/i.test(blob) ? "datetime" : "date";
    }).catch(() => "date" as const);
  }

  private async alignDatePanel(isoDate: string) {
    const [year, month] = isoDate.split("-").map(Number);
    if (!year || !month) return;
    const panel = this.page().locator(DATE_PANELS).last();
    for (let step = 0; step < 36; step += 1) {
      const header = await panel.locator(".el-date-picker__header, .el-picker-panel__icon-btn, .ant-picker-header, .arco-picker-header").first().evaluate(el => {
        const host = el.closest(".el-picker-panel, .el-date-picker, .ant-picker-dropdown, .arco-picker-container") || el.parentElement;
        return String(host?.textContent || el.textContent || "");
      }).catch(async () => panel.innerText().catch(() => ""));
      const currentYear = Number((header.match(/(\d{4})/) || [])[1]);
      let currentMonth = Number((header.match(/(\d{1,2})\s*月/) || [])[1]);
      if (!currentMonth) {
        const names = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"];
        const index = names.findIndex(name => header.toLowerCase().includes(name));
        if (index >= 0) currentMonth = index + 1;
      }
      if (currentYear === year && currentMonth === month) return;
      const forward = !currentYear || currentYear < year || (currentYear === year && (currentMonth || 0) < month);
      const next = panel.locator(".el-date-picker__next-btn, .arrow-right, .ant-picker-header-next-btn, .arco-picker-header-next, [aria-label*='next' i], [aria-label*='下一']").filter({ visible: true }).last();
      const prev = panel.locator(".el-date-picker__prev-btn, .arrow-left, .ant-picker-header-prev-btn, .arco-picker-header-prev, [aria-label*='prev' i], [aria-label*='上一']").filter({ visible: true }).last();
      const target = forward ? next : prev;
      if (!(await target.count())) return;
      await this.clickSafely(target, "option").catch(async () => target.click({ force: true, timeout: 400 }));
    }
  }

  private formatDateValue(value: string, mode: "date" | "datetime") {
    const parsed = parseFieldDate(value);
    const iso = parsed ? `${parsed.getFullYear()}-${pad2(parsed.getMonth() + 1)}-${pad2(parsed.getDate())}` : (value.match(/\d{4}-\d{2}-\d{2}/)?.[0] || localIsoDate());
    if (mode === "date") return iso;
    const time = value.match(/\d{2}:\d{2}(?::\d{2})?/)?.[0];
    return `${iso} ${time ? (time.length === 5 ? `${time}:00` : time) : "00:00:00"}`;
  }

  private async commitValue(target: Locator, value: string) {
    await target.evaluate((el, next) => {
      const node = el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement || el instanceof HTMLSelectElement
        ? el
        : el.querySelector("input, textarea, select");
      if (!(node instanceof HTMLInputElement || node instanceof HTMLTextAreaElement || node instanceof HTMLSelectElement)) {
        if (el instanceof HTMLElement && el.isContentEditable) {
          el.focus();
          el.textContent = next;
          el.dispatchEvent(new InputEvent("input", { bubbles: true, composed: true, data: next }));
          el.dispatchEvent(new Event("change", { bubbles: true }));
        }
        return;
      }
      node.focus();
      const proto = node instanceof HTMLTextAreaElement
        ? window.HTMLTextAreaElement.prototype
        : node instanceof HTMLSelectElement
          ? window.HTMLSelectElement.prototype
          : window.HTMLInputElement.prototype;
      const own = Object.getOwnPropertyDescriptor(node, "value");
      const protoSet = Object.getOwnPropertyDescriptor(proto, "value")?.set;
      (own?.set || protoSet)?.call(node, next);
      if (own?.set && protoSet && own.set !== protoSet) protoSet.call(node, next);
      node.dispatchEvent(new InputEvent("input", { bubbles: true, cancelable: true, composed: true, inputType: "insertReplacementText", data: next }));
      node.dispatchEvent(new Event("change", { bubbles: true }));
    }, value).catch(() => {});
    await this.page().evaluate(() => {
      const flush = (window as unknown as { __bssFlushUi?: (type: string, target: Element | null) => void }).__bssFlushUi;
      flush?.("change", document.activeElement);
    }).catch(() => {});
  }

  private async dateFieldTarget(locator: Locator, rangeIndex?: number) {
    const wrap = locator.locator("xpath=ancestor-or-self::*[contains(@class,'el-date-editor') or contains(@class,'el-date-picker') or contains(@class,'ant-picker') or contains(@class,'arco-picker') or contains(@class,'date-editor') or contains(@class,'picker-range') or contains(@class,'daterange')][1]");
    const group = locator.locator("xpath=ancestor-or-self::*[contains(@class,'form-item') or @role='group'][1]");
    const host = (await wrap.count()) ? wrap.first() : ((await group.count()) ? group.first() : locator);
    const nativeDate = await locator.evaluate(el => {
      const node = el instanceof HTMLInputElement ? el : el.querySelector("input");
      return Boolean(node && /^(date|datetime-local|time|month|week)$/i.test(node.getAttribute("type") || ""));
    }).catch(() => false);
    const looksDate = nativeDate || await host.evaluate(el => {
      const inputs = [...el.querySelectorAll("input")];
      return inputs.some(input => {
        const type = (input.getAttribute("type") || "").toLowerCase();
        const blob = [type, input.className, input.placeholder, el.className].join(" ");
        return /^(date|datetime-local|time|month|week)$/.test(type) || /date|time|picker|日期|时间/i.test(blob);
      });
    }).catch(() => false);
    if (!(await wrap.count()) && !looksDate) return undefined;
    const inputs = host.locator("input");
    const count = await inputs.count();
    if (!count && !nativeDate) return undefined;
    const index = rangeIndex ?? 0;
    const target = count > 1 ? inputs.nth(Math.min(index, count - 1)) : (count ? inputs.first() : locator);
    return { host, target };
  }

  async fillField(selector: string, value: string, options?: { rangeIndex?: number }) {
    const locator = await this.locate(selector);
    const dateField = await this.dateFieldTarget(locator, options?.rangeIndex);
    if (dateField) {
      const mode = await this.dateEditorMode(dateField.host);
      const filled = this.formatDateValue(value, mode);
      await this.dismissTransientOverlays();
      await dateField.target.fill(filled, { timeout: 1_200 }).catch(async () => {
        await dateField.target.fill(filled, { force: true, timeout: 800 });
      });
      await this.commitValue(dateField.target, filled);
      await dateField.target.press("Tab").catch(() => {});
      await this.page().locator(DATE_PANELS).first().waitFor({ state: "hidden", timeout: 400 }).catch(() => {});
      const current = await dateField.target.inputValue().catch(() => "");
      if (current.includes(filled.slice(0, 10))) return;
      await this.clickSafely(await this.clickTarget(selector), "field");
      if (await this.hasDatePanel()) {
        await this.alignDatePanel(filled.slice(0, 10));
        await this.pickCalendarDay(String(Number(filled.slice(8, 10))));
        await this.page().locator(DATE_PANELS).first().waitFor({ state: "hidden", timeout: 600 }).catch(() => {});
      }
      await this.commitValue(dateField.target, filled);
      return;
    }
    const chooser = await locator.evaluate(el => {
      if (el instanceof HTMLTextAreaElement) return false;
      if (el instanceof HTMLInputElement && !el.readOnly && el.getAttribute("role") !== "combobox" && !el.getAttribute("aria-haspopup")) return false;
      const role = el.getAttribute("role") || "";
      const popup = el.getAttribute("aria-haspopup") || "";
      if (role === "combobox" || /listbox|menu|dialog/i.test(popup)) return true;
      if (el.hasAttribute("readonly") && /请选择|please select/i.test(el.getAttribute("placeholder") || "")) return true;
      return Boolean(el.closest(".el-select, .ant-select, .arco-select, .n-select, .el-cascader, .el-date-editor, .ant-picker, .arco-picker"));
    }).catch(() => false);
    if (chooser) throw new Error("Control is a chooser; do not type a sample");
    const input = locator.locator("input, textarea").first();
    const target = (await input.count()) ? input : locator;
    await target.fill(value, { timeout: 1_200 }).catch(async () => {
      await target.fill(value, { force: true, timeout: 800 });
    });
    await this.commitValue(target, value);
  }

  async chooseOption(selector: string, value: string | string[]) {
    const labels = (Array.isArray(value) ? value : [value]).map(item => String(item));
    if (labels.length === 1 && /^\d{4}-\d{2}-\d{2}/.test(labels[0]!)) {
      await this.fillField(selector, labels[0]!);
      return { ok: true, url: this.page().url() };
    }
    const target = await this.clickTarget(selector);
    const page = this.page();
    const startUrl = page.url();
    for (const label of labels) {
      const scope = await this.openSelect(target);
      const option = this.optionLocator(label, scope).filter({ visible: true }).first();
      try {
        await option.waitFor({ state: "visible", timeout: 1_200 });
        if (await this.isNavigationTarget(option)) throw new Error("option is navigation");
        await this.clickSafely(option, "option");
      } catch {
        const input = target.locator("input").first();
        if (await input.count()) await input.fill(label, { timeout: 800 }).catch(() => {});
        const retryScope = await this.dropdownScope() || await this.openSelect(target);
        const retry = this.optionLocator(label, retryScope).filter({ visible: true }).first();
        await retry.waitFor({ state: "visible", timeout: 1_200 });
        if (await this.isNavigationTarget(retry)) throw new Error("Refusing to click navigation; that would leave the form and discard filled fields");
        await this.clickSafely(retry, "option");
      }
    }
    if (page.url() !== startUrl) throw new Error("Dropdown click navigated away and would discard filled fields");
    await page.locator(DROPDOWNS).first().waitFor({ state: "hidden", timeout: 400 }).catch(() => {});
    return { ok: true, url: page.url() };
  }

  async dropdownOptions(scope?: Locator) {
    const root = scope || await this.dropdownScope();
    if (!root) return [];
    return root.locator(OPTION_ITEMS).evaluateAll(elements => elements.map(el => {
      const label = String(el.textContent || "").replace(/\s+/g, " ").trim();
      const raw = el.getAttribute("value") || el.getAttribute("data-value") || el.getAttribute("data-id");
      return { label, value: raw !== undefined && raw !== "" ? raw : label };
    })).then(items => items.filter(item => item.label));
  }

  async firstVisibleOption(scope?: Locator) {
    const root = scope || await this.dropdownScope();
    if (!root) throw new Error("No open select dropdown");
    const item = root.locator(OPTION_ITEMS).filter({ visible: true }).first();
    await item.waitFor({ state: "visible", timeout: 1_200 });
    const navigates = await item.evaluate(el => Boolean(el.closest("nav, aside, .el-menu, .ant-menu, a[href]")) && !el.closest(".el-select-dropdown, .ant-select-dropdown, [role='listbox']"));
    if (navigates) throw new Error("Visible option is a navigation item; not clicking it");
    return ((await item.innerText()) || "").replace(/\s+/g, " ").trim();
  }

  async chooseFirstOption(selector: string) {
    const target = await this.clickTarget(selector);
    let scope = await this.openSelect(target);
    const item = scope.locator(OPTION_ITEMS).filter({ visible: true }).first();
    try {
      await item.waitFor({ state: "visible", timeout: 2_000 });
    } catch {
      const input = target.locator("input").first();
      if (await input.count()) {
        await input.fill("a", { timeout: 400 }).catch(() => {});
        await input.fill("", { timeout: 400 }).catch(() => {});
        scope = await this.dropdownScope() || scope;
        await scope.locator(OPTION_ITEMS).filter({ visible: true }).first().waitFor({ state: "visible", timeout: 2_000 });
      } else {
        throw new Error("Select opened but no option became visible");
      }
    }
    const options = await this.dropdownOptions(scope);
    const value = await this.firstVisibleOption(scope);
    const fieldMeta = await target.evaluate(el => {
      const attrs = ["name", "data-field", "data-name", "data-key", "data-model"];
      const generated = /^(el-id-\d+|el-[a-z]+-\d+|input-\d+|select-\d+|aria-id|:r[0-9a-z]+$)/i;
      let name: string | undefined;
      let node: Element | null = el;
      for (let i = 0; i < 8 && node; i++, node = node.parentElement) {
        if (i > 0 && node.matches("form, [role='form'], [role='dialog'], .el-dialog, .ant-modal, .arco-modal")) break;
        for (const attr of attrs) {
          const value = node.getAttribute(attr);
          if (value && !generated.test(value)) {
            name = value;
            break;
          }
        }
        if (name) break;
        if (i === 0) {
          const id = node.getAttribute("id");
          if (id && !generated.test(id)) name = id;
        }
      }
      const item = el.closest(".el-form-item, .ant-form-item, .arco-form-item, [class*='form-item']");
      const label = item?.querySelector("label, .el-form-item__label, .ant-form-item-label, .arco-form-item-label");
      const dialog = el.closest(".el-dialog, .el-drawer, .ant-modal, .arco-modal");
      return {
        label: String(label?.textContent || el.getAttribute("aria-label") || "").replace(/\s+/g, " ").trim(),
        name: name || undefined,
        scope: dialog ? "dialog" : "page"
      };
    }).catch(() => ({ label: "", name: undefined, scope: "page" as const }));
    await this.host.recordSelectObservation?.({
      label: fieldMeta.label,
      name: fieldMeta.name,
      scope: fieldMeta.scope,
      value,
      options
    });
    await this.clickSafely(this.optionLocator(value, scope).filter({ visible: true }).first(), "option");
    await this.page().locator(DROPDOWNS).first().waitFor({ state: "hidden", timeout: 250 }).catch(() => {});
    for (let level = 0; level < 3 && await this.dropdownScope(); level += 1) {
      const nextScope = await this.dropdownScope();
      if (!nextScope) break;
      const next = nextScope.locator(OPTION_ITEMS).filter({ visible: true }).first();
      if (!(await next.count())) break;
      await this.clickSafely(next, "option");
      await this.page().waitForTimeout(80);
    }
    await this.page().locator(DROPDOWNS).first().waitFor({ state: "hidden", timeout: 600 }).catch(() => {});
    return value;
  }

  async captureSnapshot(): Promise<PageSnapshot> {
    const page = this.page();
    const frames = [];
    for (const frame of page.frames()) {
      if (frame !== page.mainFrame()) await frame.waitForLoadState("domcontentloaded").catch(() => {});
      try {
        const box = await frame.locator("body").boundingBox().catch(() => null);
        const snap = await frame.locator("body").evaluate(SNAPSHOT_IN_PAGE) as PageSnapshot & { frameUrl?: string; unavailable?: boolean };
        const visible = Boolean(box && box.width >= 20 && box.height >= 20);
        frames.push({ frameUrl: frame.url(), visible, ...snap });
      } catch {
        frames.push({ frameUrl: frame.url(), unavailable: true, visible: false, formFields: [] });
      }
    }
    const main = frames[0] || {};
    const child = frames.slice(1).filter(frame => !frame.unavailable && (frame.visible || (frame.formFields || []).length > 0));
    const childFields = child.flatMap(frame => frame.formFields || []);
    const formFields = [...(main.formFields || []), ...childFields];
    const todoFields = formFields.filter(field => !field.skip && !field.disabled && !field.filled);
    const snapshot = {
      ...main,
      frames: frames.slice(1),
      formFields,
      todoFields,
      todoCount: todoFields.length,
      recentUserActions: this.host.recentUserActions()
    };
    await this.host.writePageInventory(page, snapshot);
    return snapshot;
  }

  private sampleValue(field: FormField, dateOffset = 0) {
    if (field.kind === "date") {
      const endLike = field.rangeIndex === 1 || dateOffset % 2 === 1;
      return `${localIsoDate(dateOffset)} ${endLike ? "23:59:59" : "00:00:00"}`;
    }
    if (field.kind === "number") return "1";
    const hint = String(field.label || "").replace(/[：:*：\s]/g, "").slice(0, 8);
    return hint ? `样例-${hint}` : "样例";
  }

  private async waitForFormDialog(timeout = 2_000) {
    const deadline = Date.now() + timeout;
    while (Date.now() < deadline) {
      const dialog = await this.lastFormDialog(this.page());
      if (dialog) return dialog;
      await this.page().waitForTimeout(40);
    }
    return undefined;
  }

  private async pickFirstChoice(selector: string) {
    const { before } = await this.activateChooser(selector);
    const after = await this.overlayState();
    if (after.dialogs <= before.dialogs) {
      if (after.drops !== before.drops || await this.dropdownScope()) return this.chooseFirstOption(selector);
      throw new Error("Picker did not open a dialog or list");
    }
    const dialog = await this.waitForFormDialog();
    if (!dialog) throw new Error("Picker did not open a dialog or list");
    const row = dialog.locator(DIALOG_CHOICES).filter({ visible: true }).first();
    if (!(await row.count())) throw new Error("Picker dialog has no selectable row");
    await this.clickSafely(row, "option");
    const nested = dialog.locator(DIALOG_CHOICES).filter({ visible: true }).nth(1);
    if (await this.hasDialog() && await nested.count()) await this.clickSafely(nested, "option").catch(() => {});
    const ok = dialog.locator("button, [role='button']").filter({ hasText: /^(确定|确认|选择|ok|confirm)$/i }).first();
    if (await ok.count()) await this.clickSafely(ok, "button");
    await this.waitForPageQuiet(800);
    const snapshot = await this.captureSnapshot();
    const field = (snapshot.formFields || []).find(item => item.selector === selector || item.label === selector.replace(/^label=/i, ""));
    return field?.value || "selected";
  }

  private matchField(snapshot: PageSnapshot, field: FormField) {
    const items = snapshot.formFields || [];
    const sameIndex = (item: FormField) => (item.groupIndex ?? item.rangeIndex ?? 0) === (field.groupIndex ?? field.rangeIndex ?? 0);
    return items.find(item => item.selector === field.selector && item.label === field.label)
      || items.find(item => item.label === field.label && item.name && item.name === field.name && sameIndex(item))
      || items.find(item => item.selector === field.selector && items.filter(other => other.selector === field.selector).length === 1)
      || items.find(item => item.label === field.label && sameIndex(item));
  }

  private isSampleValue(value: string, field: FormField) {
    const sample = this.sampleValue(field);
    return value === sample || value === "样例" || /^样例-/.test(value);
  }

  private strategiesFor(field: FormField, dateOffset: number) {
    const selector = field.selector || `label=${field.label}`;
    const type = async () => {
      const value = this.sampleValue(field, dateOffset);
      await this.fillField(selector, value, { rangeIndex: field.rangeIndex });
      return value;
    };
    const choose = async () => this.chooseFirstOption(selector);
    const pick = async () => this.pickFirstChoice(selector);
    const toggle = async () => {
      await this.clickSafely(await this.clickTarget(selector), "field");
      return "true";
    };
    if (field.kind === "select") return [choose, pick];
    if (field.kind === "picker") return [pick, choose];
    if (field.kind === "checkbox" || field.kind === "radio") return [toggle];
    return [type, choose, pick];
  }

  private async fillOneField(field: FormField, startUrl: string, dateOffset = 0) {
    const selector = field.selector || `label=${field.label}`;
    if (this.page().url() !== startUrl) throw new Error("Page navigated; stopping so filled fields are not overwritten");
    if (field.kind === "upload" || field.skip) return { label: field.label, selector, kind: field.kind, skipped: true };
    const chooser = field.kind === "select" || field.kind === "picker";
    let lastError: unknown;
    for (const attempt of this.strategiesFor(field, dateOffset)) {
      try {
        await this.dismissTransientOverlays();
        const value = String((await attempt()) || "");
        const after = this.matchField(await this.captureSnapshot(), field);
        if (!after?.filled) continue;
        if (chooser && this.isSampleValue(String(after.value || value), field)) continue;
        return { label: field.label, selector, kind: field.kind, value: after.value || value };
      } catch (error) {
        lastError = error;
      }
    }
    throw lastError || new Error(`Could not fill ${field.label}`);
  }

  private requiredNumberInvalid(field: FormField) {
    return field.kind === "number" && Boolean(field.required) && !field.disabled && !field.skip && isNumericZero(field.value);
  }

  private formReady(snapshot: PageSnapshot, startUrl: string) {
    const leftover = (snapshot.todoFields || []).filter(field => !field.skip && !field.disabled);
    const zeroRequired = (snapshot.formFields || []).some(field => this.requiredNumberInvalid(field));
    return leftover.length === 0 && !zeroRequired && this.page().url() === startUrl;
  }

  private async repairFormValues(startUrl: string) {
    const snapshot = await this.captureSnapshot();
    const dates = (snapshot.formFields || []).filter(field => field.kind === "date" && !field.skip && !field.disabled);
    let last: Date | undefined;
    for (const [index, field] of dates.entries()) {
      const parsed = parseFieldDate(field.value);
      if (parsed && (!last || parsed.getTime() > last.getTime())) {
        last = parsed;
        continue;
      }
      const next = new Date(last ? last.getTime() + 86_400_000 : Date.now());
      if (!last) next.setDate(next.getDate() + index);
      const iso = `${next.getFullYear()}-${pad2(next.getMonth() + 1)}-${pad2(next.getDate())}`;
      const value = `${iso} ${index % 2 === 1 ? "23:59:59" : "00:00:00"}`;
      await this.fillField(field.selector || `label=${field.label}`, value, { rangeIndex: field.rangeIndex });
      last = parseFieldDate(value);
    }
    await this.page().waitForTimeout(180);
    const afterDates = await this.captureSnapshot();
    for (const field of afterDates.formFields || []) {
      if (!this.requiredNumberInvalid(field) && !(field.invalid && field.kind === "number" && !field.disabled)) continue;
      await this.fillField(field.selector || `label=${field.label}`, "1");
    }
    void startUrl;
  }

  private submitControl(snapshot: PageSnapshot) {
    const scope = snapshot.scope;
    const controls = (snapshot.controls || []).filter(control => !scope || !control.scope || control.scope === scope);
    const scored = controls.flatMap(control => {
      const text = String(control.text || control.label || "").replace(/\s+/g, "");
      if (!text || CANCEL_LABEL.test(text)) return [];
      const button = control.tag === "button" || control.role === "button" || control.type === "submit";
      if (!button) return [];
      if (!(control.type === "submit" || SUBMIT_LABEL.test(text))) return [];
      const draft = /草稿|draft/i.test(text);
      const write = /^(提交|确定|save|submit|ok|confirm|apply)/i.test(text) && !draft;
      const search = /^(搜索|查询|search)$/i.test(text);
      return [{
        selector: String(control.selector || `text=${text}`),
        text,
        rank: write ? 0 : search ? 1 : control.type === "submit" && !draft ? 2 : draft ? 4 : 3
      }];
    });
    return scored.sort((left, right) => left.rank - right.rank)[0];
  }

  private async revealHiddenSections() {
    const tabs = this.page().locator("[role='tab'], .el-tabs__item, .ant-tabs-tab, .el-collapse-item__header").filter({ visible: true });
    const count = await tabs.count();
    for (let index = 0; index < count; index += 1) {
      const tab = tabs.nth(index);
      const selected = await tab.getAttribute("aria-selected");
      const cls = await tab.getAttribute("class") || "";
      if (selected === "true" || /is-active|ant-tabs-tab-active/.test(cls)) continue;
      await this.clickSafely(tab, "button").catch(() => {});
      await this.waitForPageQuiet(800);
      return true;
    }
    return false;
  }

  async exerciseForm() {
    await this.dismissTransientOverlays();
    const filled: Array<Record<string, unknown>> = [];
    const failed: Array<Record<string, unknown>> = [];
    const startUrl = this.page().url();
    let dateOffset = 0;
    const run = async (fields: FormField[]) => {
      for (const field of fields) {
        const offset = field.kind === "date" ? dateOffset++ : 0;
        try {
          const result = await this.fillOneField(field, startUrl, offset);
          if (!result.skipped) filled.push(result);
        } catch (error: any) {
          failed.push({ label: field.label, selector: field.selector || `label=${field.label}`, kind: field.kind, error: String(error?.message || error) });
        }
      }
    };
    let after = await this.captureSnapshot();
    for (let pass = 0; pass < 5 && this.page().url() === startUrl; pass += 1) {
      const leftover = (after.todoFields || []).filter(field => !field.skip && !field.disabled);
      if (!leftover.length) {
        if (await this.revealHiddenSections()) {
          after = await this.captureSnapshot();
          continue;
        }
        break;
      }
      await run(leftover.filter(field => field.kind !== "picker"));
      await run(leftover.filter(field => field.kind === "picker"));
      await this.dismissTransientOverlays();
      after = await this.captureSnapshot();
    }
    if (!this.formReady(after, startUrl)) {
      if (await this.revealHiddenSections()) {
        after = await this.captureSnapshot();
        await run((after.todoFields || []).filter(field => !field.skip && !field.disabled));
        after = await this.captureSnapshot();
      }
    }
    await this.repairFormValues(startUrl);
    after = await this.captureSnapshot();
    return {
      ok: this.formReady(after, startUrl),
      scope: after.scope,
      filled,
      failed,
      errors: after.errors || [],
      todoFields: after.todoFields || [],
      todoCount: after.todoCount ?? (after.todoFields || []).length,
      formFields: after.formFields || []
    };
  }

  async submitForm() {
    await this.dismissTransientOverlays();
    let before = await this.captureSnapshot();
    const startUrl = this.page().url();
    const button = this.submitControl(before);
    if (!button) throw new Error("No submit/search button in the active form");
    if (!this.formReady(before, startUrl)) {
      const leftover = (before.todoFields || []).filter(field => !field.skip && !field.disabled);
      for (const field of leftover) {
        await this.fillOneField(field, startUrl).catch(() => {});
      }
      await this.repairFormValues(startUrl);
      before = await this.captureSnapshot();
    }
    const write = /^(提交|确定|save|submit|ok|confirm|apply)/i.test(button.text) && !/搜索|查询|search/i.test(button.text);
    const pending = this.awaitFormRequest(3_000, write);
    await this.click(button.selector).catch(async () => {
      await this.click(`text=${button.text}`);
    });
    let sawRequest = await pending;
    if (!sawRequest) {
      const retry = this.awaitFormRequest(2_500, write);
      await this.page().locator("button[type='submit'], input[type='submit']").filter({ visible: true }).last().click({ timeout: 800 }).catch(() => {});
      sawRequest = await retry;
    }
    await this.waitForPageQuiet();
    let after = await this.captureSnapshot();
    const stillOpen = after.scope === before.scope && this.page().url() === startUrl;
    const blocked = stillOpen && ((after.errors || []).length > 0 || !this.formReady(after, startUrl) || (after.formFields || []).some(field => field.invalid) || !sawRequest);
    if (blocked) {
      const leftover = (after.todoFields || []).filter(field => !field.skip && !field.disabled);
      for (const field of leftover) {
        await this.fillOneField(field, startUrl).catch(() => {});
      }
      await this.repairFormValues(startUrl);
      const again = this.awaitFormRequest(3_000, write);
      await this.click(button.selector);
      sawRequest = (await again) || sawRequest;
      await this.waitForPageQuiet();
      after = await this.captureSnapshot();
    }
    const leftoverErrors = after.errors || [];
    const closed = this.page().url() !== startUrl || after.scope !== before.scope;
    const invalid = (after.formFields || []).some(field => field.invalid || this.requiredNumberInvalid(field));
    const leftoverTodos = (after.todoFields || []).filter(field => !field.skip && !field.disabled);
    return {
      ok: closed || Boolean(sawRequest && leftoverErrors.length === 0 && !invalid && leftoverTodos.length === 0),
      submitted: button.text,
      repaired: blocked,
      sawRequest,
      errors: leftoverErrors,
      url: this.page().url(),
      scope: after.scope,
      todoFields: after.todoFields || [],
      todoCount: after.todoCount ?? (after.todoFields || []).length,
      formFields: after.formFields || []
    };
  }
}
