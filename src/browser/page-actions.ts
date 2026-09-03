import type { Frame, Locator, Page } from "playwright";
import { SNAPSHOT_IN_PAGE } from "./page-script.js";

export const FORM_ITEMS = ".el-form-item, .ant-form-item, .arco-form-item, .n-form-item, .van-field, [class*='form-item']";
export const FORM_LABELS = "label, .el-form-item__label, .ant-form-item-label, .arco-form-item-label, .n-form-item-label, .van-field__label";
export const DIALOGS = "[role='dialog']:visible, [role='alertdialog']:visible, .el-dialog:visible, .el-drawer:visible, .ant-modal:visible, .ant-drawer-content:visible, .arco-modal:visible, .arco-drawer:visible";
export const DROPDOWNS = ".el-select-dropdown:visible, .el-cascader__dropdown:visible, .el-autocomplete-suggestion:visible, .ant-select-dropdown:visible, .arco-select-dropdown:visible, [role='listbox']:visible";
export const DATE_PANELS = ".el-picker-panel:visible, .el-popper.el-date-picker:visible, .el-picker__popper:visible, .ant-picker-dropdown:visible, .arco-picker-container:visible, [class*='picker-dropdown']:visible, [class*='picker-panel']:visible";
export const OPTION_ITEMS = "[role='option'], .el-select-dropdown__item, .el-cascader-node, .el-autocomplete-suggestion__list li, .ant-select-item-option, .arco-select-option, .n-base-select-option";
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

  private async awaitFormRequest(timeout = 2_500) {
    await this.page().waitForResponse(response => {
      const type = response.request().resourceType();
      return type === "xhr" || type === "fetch";
    }, { timeout }).catch(() => {});
  }

  async hasDatePanel() {
    return (await this.page().locator(DATE_PANELS).count()) > 0;
  }

  async hasDialog() {
    return Boolean(await this.lastFormDialog(this.page()));
  }

  private async lastFormDialog(root: Frame | Locator | Page) {
    const all = root.locator(DIALOGS);
    const count = await all.count();
    for (let index = count - 1; index >= 0; index -= 1) {
      const item = all.nth(index);
      const formDialog = await item.evaluate(el => !/picker-panel|picker-dropdown|datepicker|date-picker/i.test(el.className || "")).catch(() => true);
      if (formDialog) return item;
    }
    return undefined;
  }

  locatorIn(root: Frame | Locator, selector: string) {
    const placeholder = selector.match(/^placeholder=(.+)$/s);
    if (placeholder) return root.getByPlaceholder(placeholder[1]!);
    const label = selector.match(/^label=(.+)$/s);
    if (label) {
      const name = label[1]!;
      const exact = exactText(name);
      const labeled = root.locator(FORM_LABELS).filter({ hasText: exact });
      return root.getByLabel(name, { exact: true })
        .or(root.getByPlaceholder(name, { exact: true }))
        .or(root.getByPlaceholder(new RegExp(`^(请选择|请输入|请填写|please select|please enter|please choose|select)?\\s*${escapeRegExp(name)}$`, "i")))
        .or(root.getByRole("combobox", { name, exact: true }))
        .or(root.getByRole("textbox", { name, exact: true }))
        .or(root.locator(FORM_ITEMS).filter({
          has: labeled
        }).locator("input, textarea, select, [role='combobox'], .el-select__wrapper, .el-date-editor, .ant-select-selector, .ant-picker").first())
        .or(labeled.locator("xpath=following::*[self::input or self::textarea or self::select or @role='combobox'][1]"));
    }
    const text = selector.match(/^text=(.+)$/s);
    if (text) return root.getByText(text[1]!, { exact: true });
    const role = selector.match(/^role=([a-z]+)(?:\[name=["'](.+)["']\])?$/i);
    if (role) return root.getByRole(role[1] as "button", role[2] ? { name: role[2] } : {});
    return root.locator(selector);
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
          const found = this.locatorIn(scope, selector).filter({ visible: true }).first();
          if (await found.count()) {
            if (textOnly && await this.isNavigationTarget(found)) continue;
            return found;
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
        if (text !== name) continue;
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
      return Boolean(el.closest("nav, aside, .el-menu, .ant-menu, .el-menu-item, .ant-menu-item, .el-pagination, .ant-pagination"));
    }).catch(() => false);
  }

  async clickTarget(selector: string) {
    const locator = await this.locate(selector);
    const selectSurface = locator.locator("xpath=ancestor-or-self::*[contains(@class,'el-select__wrapper') or contains(@class,'ant-select-selector') or contains(@class,'arco-select-view')][1]");
    if (await selectSurface.count()) return selectSurface.first();
    if (!this.isFieldSelector(selector)) return locator;
    const surface = locator.locator(WIDGET_SURFACES);
    if (await surface.count()) return surface.first();
    const inner = locator.locator("input, textarea, [role='combobox']").first();
    if (await inner.count()) return inner;
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
      const nav = el.closest("nav, aside, .el-menu, .ant-menu, .el-menu-item, .ant-menu-item, .el-pagination, .ant-pagination");
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
        if (el.closest("nav, aside, .el-menu, .ant-menu") && !el.closest(".el-select-dropdown,[role='listbox'],.el-dialog,[role='dialog']")) return false;
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

  async openSelect(target: Locator) {
    await this.dismissTransientOverlays();
    await this.clickSafely(target, "field");
    let scope = await this.waitForDropdown(1_600);
    if (!scope) {
      const input = target.locator("input").first();
      if (await input.count()) {
        await input.press("ArrowDown").catch(() => {});
        scope = await this.waitForDropdown(800);
      }
    }
    if (!scope) {
      await this.clickSafely(target, "field");
      scope = await this.waitForDropdown(800);
    }
    if (!scope) throw new Error("Select dropdown did not open");
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
      Object.getOwnPropertyDescriptor(proto, "value")?.set?.call(node, next);
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
    await this.page().locator(DROPDOWNS).first().waitFor({ state: "hidden", timeout: 600 }).catch(() => {});
    return value;
  }

  async captureSnapshot(): Promise<PageSnapshot> {
    const page = this.page();
    const frames = [];
    for (const frame of page.frames()) {
      try {
        frames.push({ frameUrl: frame.url(), ...(await frame.locator("body").evaluate(SNAPSHOT_IN_PAGE) as object) });
      } catch {
        frames.push({ frameUrl: frame.url(), unavailable: true });
      }
    }
    const snapshot = { ...frames[0], frames: frames.slice(1), recentUserActions: this.host.recentUserActions() };
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

  private async fillOneField(field: FormField, startUrl: string, dateOffset = 0) {
    const selector = field.selector || `label=${field.label}`;
    if (this.page().url() !== startUrl) throw new Error("Page navigated; stopping so filled fields are not overwritten");
    if (field.kind === "upload" || field.skip) return { label: field.label, selector, kind: field.kind, skipped: true };
    await this.dismissTransientOverlays();
    if (field.kind === "select") {
      const value = await this.chooseFirstOption(selector);
      return { label: field.label, selector, kind: field.kind, value };
    }
    if (field.kind === "checkbox" || field.kind === "radio") {
      await this.clickSafely(await this.clickTarget(selector), "field");
      return { label: field.label, selector, kind: field.kind, value: "true" };
    }
    const value = this.sampleValue(field, dateOffset);
    await this.fillField(selector, value, { rangeIndex: field.rangeIndex });
    return { label: field.label, selector, kind: field.kind, value };
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

  async exerciseForm() {
    await this.dismissTransientOverlays();
    const before = await this.captureSnapshot();
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
    await run(before.todoFields || []);
    await this.dismissTransientOverlays();
    await this.page().waitForTimeout(180);
    let after = await this.captureSnapshot();
    if ((after.todoFields || []).length) {
      await run(after.todoFields || []);
      await this.dismissTransientOverlays();
      after = await this.captureSnapshot();
    }
    if (!this.formReady(after, startUrl)) {
      await this.repairFormValues(startUrl);
      after = await this.captureSnapshot();
    }
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
    const before = await this.captureSnapshot();
    const startUrl = this.page().url();
    const button = this.submitControl(before);
    if (!button) throw new Error("No submit/search button in the active form");
    if (!this.formReady(before, startUrl)) {
      await this.repairFormValues(startUrl);
    }
    await this.click(button.selector).catch(async () => {
      await this.click(`text=${button.text}`);
    });
    await this.awaitFormRequest();
    await this.waitForPageQuiet();
    let after = await this.captureSnapshot();
    const stillOpen = after.scope === before.scope && this.page().url() === startUrl;
    const blocked = stillOpen && ((after.errors || []).length > 0 || !this.formReady(after, startUrl) || (after.formFields || []).some(field => field.invalid));
    if (blocked) {
      await this.repairFormValues(startUrl);
      await this.click(button.selector);
      await this.awaitFormRequest();
      await this.waitForPageQuiet();
      after = await this.captureSnapshot();
    }
    const leftoverErrors = after.errors || [];
    const closed = this.page().url() !== startUrl || after.scope !== before.scope;
    const invalid = (after.formFields || []).some(field => field.invalid || this.requiredNumberInvalid(field));
    return {
      ok: closed || (leftoverErrors.length === 0 && !invalid),
      submitted: button.text,
      repaired: blocked,
      errors: leftoverErrors,
      url: this.page().url(),
      scope: after.scope,
      todoFields: after.todoFields || [],
      todoCount: after.todoCount ?? (after.todoFields || []).length,
      formFields: after.formFields || []
    };
  }
}
