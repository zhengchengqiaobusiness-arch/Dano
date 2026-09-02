import type { Frame, Locator, Page } from "playwright";
import { SNAPSHOT_IN_PAGE } from "./page-script.js";

export const FORM_ITEMS = ".el-form-item, .ant-form-item, .arco-form-item, .n-form-item, .van-field";
export const FORM_LABELS = "label, .el-form-item__label, .ant-form-item-label, .arco-form-item-label, .n-form-item-label, .van-field__label";
export const DIALOGS = ".el-dialog:visible, .el-drawer:visible, .ant-modal:visible, .ant-drawer-content:visible, .arco-modal:visible, .arco-drawer:visible";
export const DROPDOWNS = ".el-select-dropdown:visible, .el-cascader__dropdown:visible, .el-autocomplete-suggestion:visible, .ant-select-dropdown:visible, .arco-select-dropdown:visible, [role='listbox']:visible";
export const DATE_PANELS = ".el-picker-panel:visible, .el-popper.el-date-picker:visible, .ant-picker-dropdown:visible, .arco-picker-container:visible";
export const OPTION_ITEMS = "[role='option'], .el-select-dropdown__item, .el-cascader-node, .el-autocomplete-suggestion__list li, .ant-select-item-option, .arco-select-option, .n-base-select-option";
export const WIDGET_SURFACES = "xpath=ancestor-or-self::*[contains(@class,'el-select__wrapper') or contains(@class,'el-input__wrapper') or contains(@class,'el-date-editor') or contains(@class,'ant-select-selector') or contains(@class,'ant-picker') or contains(@class,'arco-select-view') or contains(@class,'arco-picker')][1]";
export const BUSY_SPINNERS = ".el-loading-mask:visible, .el-overlay.is-loading:visible, .nprogress-busy:visible, .ant-spin-spinning:visible, .arco-spin-loading:visible";

export interface FormField {
  label: string;
  name?: string;
  selector: string;
  kind: string;
  filled: boolean;
  skip: boolean;
  disabled: boolean;
  required?: boolean;
  value?: string;
  scope?: string;
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
  frames?: unknown[];
  recentUserActions?: unknown[];
}

export interface PageActionHost {
  page(): Page;
  writePageInventory(page: Page, snapshot: PageSnapshot): Promise<void>;
  recentUserActions(): unknown[];
}

function escapeRegExp(value: string) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function exactText(value: string) {
  return new RegExp(`^\\s*${escapeRegExp(value)}\\s*$`);
}

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

  async hasDatePanel() {
    return (await this.page().locator(DATE_PANELS).count()) > 0;
  }

  async hasDialog() {
    return (await this.page().locator(DIALOGS).count()) > 0;
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
        .or(root.getByPlaceholder(new RegExp(`^(请选择|请输入|请填写)?${escapeRegExp(name)}$`)))
        .or(root.getByRole("combobox", { name, exact: true }))
        .or(root.getByRole("textbox", { name, exact: true }))
        .or(root.locator(FORM_ITEMS).filter({
          has: labeled
        }).locator("input, textarea, select, [role='combobox'], .el-select__wrapper, .el-date-editor, .ant-select-selector, .ant-picker").first())
        .or(labeled.locator("xpath=following::*[self::input or self::textarea or self::select or @role='combobox'][1]"))
        .or(root.getByPlaceholder(new RegExp(`^(请选择|请输入|请填写)?${escapeRegExp(name.replace(/名称$/, ""))}$`)));
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
        const dialog = frame.locator(DIALOGS).last();
        const dropdown = frame.locator(DROPDOWNS).last();
        const scopes: Array<Frame | Locator> = [];
        if (textOnly && await dropdown.count()) scopes.push(dropdown);
        if (await dialog.count()) scopes.push(dialog);
        if (!fieldOnly || !(await dialog.count())) scopes.push(frame);
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
    const cell = panel.locator(".el-date-table-cell__text, .el-date-table-cell, td.available .cell, td.available, .ant-picker-cell-inner")
      .filter({ hasText: exactText(day) }).last();
    await this.clickSafely(cell, "option");
  }

  async dismissTransientOverlays() {
    const page = this.page();
    try {
      if (await this.hasDatePanel()) {
        const panel = page.locator(DATE_PANELS).last();
        const today = panel.locator("td.today, td.available.today, .el-date-table td.is-today, .today, .ant-picker-cell-today .ant-picker-cell-inner").first();
        const day = (await today.count())
          ? today
          : panel.locator(".el-date-table-cell__text, td.available .cell, .ant-picker-cell-inner").filter({ visible: true }).first();
        if (await day.count()) {
          await this.clickSafely(day, "option").catch(async () => {
            await day.click({ force: true, timeout: 400 });
          });
        }
        await page.locator(DATE_PANELS).first().waitFor({ state: "hidden", timeout: 500 }).catch(() => {});
      }
      if (await this.dropdownScope()) {
        const opener = page.locator(".el-select__wrapper.is-focused, .el-select.is-focused .el-select__wrapper, .ant-select-open .ant-select-selector").first();
        if (await opener.count()) await this.clickSafely(opener, "field").catch(() => {});
        else {
          const label = page.locator(`${DIALOGS} ${FORM_LABELS}, ${FORM_LABELS}`).filter({ visible: true }).first();
          if (await label.count()) await label.click({ timeout: 400 }).catch(() => {});
        }
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

  async fillField(selector: string, value: string) {
    const locator = await this.locate(selector);
    const dateWrap = locator.locator("xpath=ancestor-or-self::*[contains(@class,'el-date-editor') or contains(@class,'el-date-picker') or contains(@class,'ant-picker')][1]");
    if (await dateWrap.count()) {
      await this.dismissTransientOverlays();
      await this.clickSafely(await this.clickTarget(selector), "field");
      if (await this.hasDatePanel()) {
        const day = /^\d{4}-\d{2}-\d{2}/.test(value.trim()) ? String(Number(value.trim().slice(8, 10))) : String(new Date().getDate());
        await this.pickCalendarDay(day);
        await this.page().locator(DATE_PANELS).first().waitFor({ state: "hidden", timeout: 600 }).catch(() => {});
        return;
      }
    }
    const input = ((await dateWrap.count()) ? dateWrap : locator).locator("input, textarea").first();
    const target = (await input.count()) ? input : locator;
    const filled = (await dateWrap.count()) && /^\d{4}-\d{2}-\d{2}$/.test(value.trim())
      ? `${value.trim()} 00:00:00`
      : value;
    await target.fill(filled, { timeout: 1_200 }).catch(async () => {
      await target.fill(filled, { force: true, timeout: 800 });
    });
    if (await dateWrap.count()) await target.press("Tab").catch(() => {});
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
    const value = await this.firstVisibleOption(scope);
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

  private sampleValue(field: FormField) {
    if (field.kind === "date") return new Date().toISOString().slice(0, 10);
    if (field.kind === "number") return "1";
    const hint = String(field.label || "").replace(/[：:*：\s]/g, "").slice(0, 8);
    return hint ? `样例-${hint}` : "样例";
  }

  private async fillOneField(field: FormField, startUrl: string) {
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
    const value = this.sampleValue(field);
    await this.fillField(selector, value);
    return { label: field.label, selector, kind: field.kind, value };
  }

  async exerciseForm() {
    await this.dismissTransientOverlays();
    const before = await this.captureSnapshot();
    const filled: Array<Record<string, unknown>> = [];
    const failed: Array<Record<string, unknown>> = [];
    const startUrl = this.page().url();
    const run = async (fields: FormField[]) => {
      for (const field of fields) {
        try {
          const result = await this.fillOneField(field, startUrl);
          if (!result.skipped) filled.push(result);
        } catch (error: any) {
          failed.push({ label: field.label, selector: field.selector || `label=${field.label}`, kind: field.kind, error: String(error?.message || error) });
        }
      }
    };
    await run(before.todoFields || []);
    await this.dismissTransientOverlays();
    let after = await this.captureSnapshot();
    if ((after.todoFields || []).length) {
      await run(after.todoFields || []);
      await this.dismissTransientOverlays();
      after = await this.captureSnapshot();
    }
    return {
      ok: (after.todoFields || []).length === 0 && this.page().url() === startUrl,
      scope: after.scope,
      filled,
      failed,
      todoFields: after.todoFields || [],
      todoCount: after.todoCount ?? (after.todoFields || []).length,
      formFields: after.formFields || []
    };
  }
}
