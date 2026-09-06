/**
 * 文件级说明：表单策略的主流判断已交给 `.pi/skills/operate-form-controls`。
 * 本文件只提供 click/fill/choose/snapshot 原语，供 Pi 按 snapshot 逐项操作。
 * 旧组件库百科全书见 `page-actions.ts.bak`。
 */
import type { Frame, Locator, Page } from "playwright";
import type { OperationKind } from "../domain.js";
import { PATCH_VUE_FORM_COMPANIONS } from "./form-companions.js";
import { MARK_LABELED_CONTROL, SNAPSHOT_FIELDS_IN_PAGE, SNAPSHOT_IN_PAGE } from "./page-script.js";
import { authenticationFailureReason, businessResponseFailureReason, inferUiOperationIntent } from "../inference/heuristics.js";

export const FORM_ITEMS = ".el-form-item, .ant-form-item, .arco-form-item, .n-form-item, .van-field, [data-slot='form-item'], [class*='form-item']";
export const FORM_LABELS = "label, .el-form-item__label, .ant-form-item-label, .arco-form-item-label, .n-form-item-label, .van-field__label, [data-slot='form-label']";
export const DIALOGS = "[role='dialog']:visible, [role='alertdialog']:visible, .el-dialog:visible, .el-drawer:visible, .ant-modal:visible, .ant-drawer-content:visible, .arco-modal:visible, .arco-drawer:visible";
export const PORTAL_MENUS = "[data-reka-popper-content-wrapper], [data-radix-popper-content-wrapper], [data-state='open'][data-slot='popover-content'], [data-state='open'][data-slot='select-content'], [data-state='open'][data-slot='combobox-content'], [data-state='open'][data-slot='dropdown-menu-content']";
export const DROPDOWNS = ".el-select-dropdown:visible, .el-select__popper:visible, .el-popper.el-select__popper:visible, .el-cascader__dropdown:visible, .el-autocomplete-suggestion:visible, .ant-select-dropdown:visible, .ant-tree-select-dropdown:visible, .arco-select-dropdown:visible, .arco-select-popup:visible, .arco-tree-select-popup:visible, .arco-cascader-popup:visible, .arco-trigger-popup:visible, [class*='select-popup']:visible, [class*='tree-select-popup']:visible, [class*='cascader-popup']:visible, [class*='trigger-popup']:visible, [role='listbox']:visible, [data-reka-popper-content-wrapper]:visible, [data-radix-popper-content-wrapper]:visible, [data-state='open'][data-slot='popover-content']:visible, [data-state='open'][data-slot='select-content']:visible, [data-state='open'][data-slot='combobox-content']:visible, [data-state='open'][data-slot='dropdown-menu-content']:visible";
export const DATE_PANELS = ".el-picker-panel:visible, .el-popper.el-date-picker:visible, .el-picker__popper:visible, .el-date-range-picker:visible, .el-time-panel:visible, .ant-picker-dropdown:visible, .arco-picker-container:visible, [class*='picker-dropdown']:visible, [class*='picker-panel']:visible";
const ACTIVE_DATE_OVERLAYS = ".ant-picker-dropdown:visible, .el-picker__popper:visible, .el-popper.el-date-picker:visible, .el-date-range-picker:visible, .el-date-picker:visible, .arco-picker-container:visible, [class*='picker-dropdown']:visible";
const PICKER_DIALOG = /picker-panel|picker-dropdown|picker__popper|el-date-picker|el-date-range-picker|el-time-panel|el-time-picker|ant-picker-dropdown|arco-picker-container|datepicker/i;
export const OPTION_ITEMS = "[role='option'], [role='treeitem'], .el-select-dropdown__item, .el-cascader-node, .el-tree-node__content, .el-autocomplete-suggestion__list li, .ant-select-item-option, .ant-select-tree-title, .ant-tree-title, .ant-cascader-menu-item, .arco-select-option, .arco-tree-node-title, .arco-cascader-option, .n-base-select-option";
export const DIALOG_CHOICES = "[role='option'], [role='treeitem'], [role='listitem'], tbody [role='row'], tbody tr, .el-table__body .el-table__row, .ant-table-tbody .ant-table-row, .vxe-table--body .vxe-body--row, .vxe-body--row, .el-tree-node__content, .el-cascader-node, [role='radio'], .el-checkbox";
const TABLE_DATA_ROWS = "tbody tr:not(.ant-table-measure-row):not(.ant-table-placeholder):not(.vxe-header--row), .el-table__body .el-table__row, .ant-table-tbody .ant-table-row:not(.ant-table-measure-row):not(.ant-table-placeholder), .vxe-table--body tbody tr.vxe-body--row, .vxe-body--row";
export const WIDGET_SURFACES = "xpath=ancestor-or-self::*[contains(@class,'el-select__wrapper') or contains(@class,'el-input__wrapper') or contains(@class,'el-date-editor') or contains(@class,'ant-select-selector') or contains(@class,'ant-picker') or contains(@class,'arco-select-view') or contains(@class,'arco-picker') or contains(@class,'picker-range') or contains(@class,'date-editor') or @role='combobox' or contains(@data-slot,'select-trigger') or contains(@data-slot,'combobox-trigger')][1]";
export const BUSY_SPINNERS = "body > .loading:visible, .el-loading-mask:visible, .el-overlay.is-loading:visible, .nprogress-busy:visible, .ant-spin-spinning:visible, .arco-spin-loading:visible, [aria-busy='true']:visible";

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
  error?: string;
  value?: string;
  type?: string;
  options?: Array<{ value: unknown; label: string }>;
  scope?: "page" | "dialog";
  rangeIndex?: number;
  groupIndex?: number;
}

export interface PageSnapshot {
  title?: string;
  pageHeading?: string;
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
  recordedManualSteps?: unknown[];
  followManualSteps?: boolean;
  availableOperations?: OperationKind[];
  operationInventory?: Array<{
    operation: OperationKind;
    label: string;
    selector?: string;
    enabled: boolean;
    frameUrl?: string;
  }>;
  navigationInventory?: Array<{
    label: string;
    selector: string;
    url: string;
    visited?: boolean;
    current?: boolean;
  }>;
  navigationCoverage?: {
    discovered: number;
    visited: number;
    remaining: number;
    unvisited: Array<{ label: string; selector: string; url: string }>;
  };
}

export interface PageActionHost {
  page(): Page;
  writePageInventory(page: Page, snapshot: PageSnapshot): Promise<void>;
  recentUserActions(): unknown[];
  drainNetwork?(timeout?: number): Promise<void>;
  recordedManualSteps?(): unknown[];
  followManualSteps?(): boolean;
  recordSelectObservation?(info: {
    label?: string;
    name?: string;
    scope?: "page" | "dialog";
    value?: string;
    options: Array<{ value: unknown; label: string }>;
  }): Promise<unknown>;
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

const WRITE_SUBMIT_LABEL = /^(确认提交|确认保存|提交申请|保存并提交|提交|确定|保存|submit|save|ok|confirm|apply)/i;
const SUBMIT_LABEL = /^(确认提交|确认保存|提交申请|保存并提交|提交|确定|保存|搜索|查询|search|submit|save|ok|confirm|apply)/i;
const CANCEL_LABEL = /取消|关闭|重置|reset|cancel|close|back/i;

export class PageActions {
  private readonly expandedRepeatables = new Set<string>();

  constructor(private readonly host: PageActionHost) {}

  private page() {
    return this.host.page();
  }

  async waitForPageQuiet(timeout = 600) {
    const busy = this.page().locator(BUSY_SPINNERS);
    if (await busy.count()) {
      await busy.first().waitFor({ state: "hidden", timeout }).catch(() => {});
    }
    await this.host.drainNetwork?.(Math.max(timeout, 1_200));
  }

  async isStartupSplash() {
    return this.page().evaluate(() => {
      const rootLoading = document.querySelector("body > .loading, body > [class*='loading'], #app:empty") !== null;
      const interactive = document.querySelector("button,a[href],input,select,textarea,[role='button'],[role='menuitem']") !== null;
      const text = String(document.body?.innerText || "").replace(/\s+/g, " ").trim();
      return !interactive && text.length <= 300 && (rootLoading || /加载中|正在加载|loading|starting/i.test(text));
    }).catch(() => false);
  }

  async nudgeOverlayFrames() {
    await this.page().evaluate(() => {
      for (const iframe of document.querySelectorAll("iframe")) {
        const style = getComputedStyle(iframe);
        if (style.display === "none" || style.visibility === "hidden") continue;
        const box = iframe.getBoundingClientRect();
        const parent = iframe.parentElement?.getBoundingClientRect();
        if (!parent || parent.height < 160 || parent.width < 120 || box.height >= 48) continue;
        iframe.style.boxSizing = "border-box";
        iframe.style.width = "100%";
        iframe.style.minHeight = "160px";
        iframe.style.height = `${Math.floor(parent.height)}px`;
        iframe.style.flex = "1 1 auto";
      }
    }).catch(() => {});
  }

  private async awaitFormRequest(timeout = 2_500, write = false) {
    try {
      const response = await this.page().waitForResponse(response => {
        const request = response.request();
        const type = request.resourceType();
        if (type !== "xhr" && type !== "fetch") return false;
        if (write) return /^(POST|PUT|PATCH|DELETE)$/i.test(request.method());
        return true;
      }, { timeout });
      const text = await response.text().catch(() => "");
      let body: unknown = text;
      if (text) {
        try { body = JSON.parse(text); } catch { /* keep text response */ }
      }
      return {
        status: response.status(),
        body,
        url: response.url(),
        method: response.request().method()
      };
    } catch {
      return undefined;
    }
  }

  async hasDatePanel() {
    return (await this.page().locator(DATE_PANELS).count()) > 0;
  }

  async hasDialog() {
    return Boolean(await this.lastFormDialog(this.page()));
  }

  private async isChooserDialog(item: Locator) {
    return item.evaluate(el => {
      const title = String((el.querySelector(".el-dialog__title, .el-dialog__header, .ant-modal-title, .arco-modal-title, .el-drawer__title, [class*='dialog__title'], [class*='dialog-header'], [class*='modal-title']") || {}).textContent || "").replace(/\s+/g, " ").trim();
      const formItems = el.querySelectorAll(".el-form-item, .ant-form-item, .arco-form-item").length;
      const rows = el.querySelectorAll("tbody tr, .el-table__body .el-table__row, .ant-table-tbody .ant-table-row, .vxe-body--row, .el-tree-node").length;
      const confirm = [...el.querySelectorAll("button, [role='button']")].some(btn => /^(确\s*定|确\s*认|选\s*择|ok|confirm)$/i.test(String(btn.textContent || "").replace(/\s+/g, "")));
      const tree = el.querySelectorAll(".el-tree, [role='tree'], .el-tree-node").length;
      const selectionTitle = /(?:^|[：:\-—])(请选择|选择|挑选|选取)|^(请选择|选择|挑选|选取)|(?:选择|挑选|选取)$|\b(?:choose|select|picker)\b/i.test(title);
      if (selectionTitle) return true;
      if (tree >= 1 && confirm && formItems <= 8) return true;
      return rows >= 1 && formItems <= 3 && confirm;
    }).catch(() => false);
  }

  private async isFormDialog(item: Locator) {
    if (await this.isChooserDialog(item)) return false;
    return item.evaluate((el, pattern) => !new RegExp(pattern, "i").test(String(el.className || "")), PICKER_DIALOG.source).catch(() => true);
  }

  private async lastChooserDialog(root?: Frame | Locator | Page) {
    const all = (root || this.page()).locator(DIALOGS);
    const count = await all.count();
    for (let index = count - 1; index >= 0; index -= 1) {
      const item = all.nth(index);
      if (await this.isChooserDialog(item)) return item;
    }
    return undefined;
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

  private scopeRoot(root: Page | Frame | Locator) {
    return typeof (root as Page | Frame).url === "function" ? (root as Page | Frame).locator("body") : root as Locator;
  }

  private async uniqueField(root: Frame | Locator, name: string) {
    const scope = this.scopeRoot(root);
    for (const candidate of [
      scope.getByRole("combobox", { name, exact: true }),
      scope.getByRole("textbox", { name, exact: true }),
      scope.getByRole("radio", { name, exact: true }),
      scope.getByRole("checkbox", { name, exact: true }),
      scope.getByRole("button", { name, exact: true }),
      scope.getByPlaceholder(name, { exact: true })
    ]) {
      const unique = candidate.filter({ visible: true });
      if (await unique.count() === 1) return unique.first();
    }
    return undefined;
  }

  private async uniquePlaceholder(found: Locator, selector: string) {
    const placeholder = selector.match(/^placeholder=(.+)$/s)?.[1] || "";
    const count = await found.count();
    const scored: Array<{ item: Locator; textarea: boolean; inTable: boolean; inForm: boolean }> = [];
    for (let index = 0; index < count; index += 1) {
      const item = found.nth(index);
      const meta = await item.evaluate(el => ({
        textarea: el.tagName === "TEXTAREA",
        inTable: Boolean(el.closest("tbody, .el-table__body, .ant-table-tbody, td, .el-table__cell")),
        inForm: Boolean(el.closest(".el-form-item, .ant-form-item, .arco-form-item"))
      })).catch(() => undefined);
      if (meta) scored.push({ item, ...meta });
    }
    const formOnly = scored.filter(item => item.inForm && !item.inTable);
    if (formOnly.length === 1) return formOnly[0]!.item;
    const textarea = formOnly.filter(item => item.textarea);
    if (textarea.length === 1) return textarea[0]!.item;
    if (!placeholder) return undefined;
    const name = placeholder.replace(/^(请输入|请选择|请填写|please enter|please select|please choose)\s*/i, "");
    if (name && name !== placeholder) {
      const labeled = await this.labeledControl(this.page(), name);
      if (labeled) return labeled;
    }
    return undefined;
  }

  private async labeledControl(root: Page | Frame | Locator, name: string) {
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
    return found;
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
    return /^(label|placeholder|column)=/i.test(selector);
  }

  private widgetAlias(selector: string) {
    if (!selector || /^(text|label|placeholder|role|column)=/i.test(selector)) return "";
    if (!/checkbox/i.test(selector)) return "";
    return ".vxe-cell--checkbox, .vxe-checkbox--icon, .vxe-table-icon-checkbox-unchecked, .col--checkbox .vxe-cell, .ant-checkbox, .el-checkbox, input[type=checkbox]";
  }

  async locate(selector: string, options: { allowNavigation?: boolean } = {}) {
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
        scopes.push(frame);
        for (const scope of scopes) {
          if (fieldOnly && /^column=/i.test(selector)) {
            const table = await this.tableControl(scope, selector.replace(/^column=/i, ""));
            if (table) return table;
            continue;
          }
          if (fieldOnly && /^label=/i.test(selector)) {
            const name = selector.replace(/^label=/i, "");
            const labeled = await this.labeledControl(scope, name);
            if (labeled) return labeled;
            const table = await this.tableControl(scope, name);
            if (table) return table;
            const unique = await this.uniqueField(scope, name);
            if (unique) return unique;
            const row = await this.tableRowByLabel(scope, name);
            if (row) return row;
            continue;
          }
          const found = this.locatorIn(scope, selector).filter({ visible: true });
          let count = await found.count();
          if (!count && !fieldOnly && !textOnly && !dayOnly) {
            const alias = this.widgetAlias(selector);
            if (alias) {
              const chooser = await this.lastChooserDialog(frame);
              const fallback = this.locatorIn(chooser || scope, alias).filter({ visible: true });
              if (await fallback.count()) return fallback.first();
            }
          }
          if (count === 1) {
            const item = found.first();
            if (textOnly && !options.allowNavigation && await this.isNavigationTarget(item)) continue;
            return item;
          }
          if (count > 1 && !fieldOnly && !textOnly && dialog && scope === dialog) return found.first();
          if (count > 1 && fieldOnly) {
            const resolved = await this.uniquePlaceholder(found, selector);
            if (resolved) return resolved;
            continue;
          }
          if (count > 1 && textOnly && ((dialog && scope === dialog) || (await dropdown.count() && scope === dropdown))) {
            const item = found.first();
            if (options.allowNavigation || !(await this.isNavigationTarget(item))) return item;
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
    if (this.isFieldSelector(selector)) {
      const target = await this.clickTarget(selector);
      if (await this.isTableDataRow(target)) {
        await this.domActivateRow(target);
        return { ok: true, url: this.page().url() };
      }
      await this.hostClick(target);
      return { ok: true, url: this.page().url() };
    }
    const target = await this.locate(selector, { allowNavigation: true });
    if (await this.isChooserCheckbox(target) || await this.isTableDataRow(target)) {
      await this.domActivateRow(target);
      return { ok: true, url: this.page().url() };
    }
    const navigation = await this.isNavigationTarget(target);
    if (navigation) {
      const snapshot = await this.captureSnapshot();
      const hasWriteSubmit = (snapshot.controls || []).some(control => /^(提交|确定|保存|申请|审核|通过|驳回|save|submit|confirm|apply)$/i
        .test(String(control.text || control.label || "").replace(/\s+/g, "")));
      const hasEnteredValues = (snapshot.formFields || []).some(field => field.filled && !field.disabled && !field.skip);
      if (hasWriteSubmit && hasEnteredValues) throw new Error("Refusing to leave a write form with entered values");
    }
    const label = String(
      await target.innerText().catch(() => "")
      || await target.getAttribute("aria-label")
      || await target.getAttribute("title")
      || ""
    ).replace(/\s+/g, "");
    if (WRITE_SUBMIT_LABEL.test(label) && !/搜索|查询|search/i.test(label)) {
      await this.patchVueFormCompanions();
    }
    await this.clickSafely(target, navigation ? "navigation" : "button");
    return { ok: true, url: this.page().url() };
  }

  async tableControl(root: Frame | Locator, name: string) {
    const tables = root.locator(".el-table, .ant-table, .arco-table, .vxe-table, table");
    const tableCount = await tables.count();
    for (let tableIndex = 0; tableIndex < tableCount; tableIndex += 1) {
      const table = tables.nth(tableIndex);
      const headers = table.locator(".el-table__header th, .el-table__header-wrapper th, thead th, .ant-table-thead th, .el-table__header .el-table__cell, .arco-table-th");
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
      const inPicker = el.closest(".el-select-dropdown,.ant-select-dropdown,.el-picker-panel,.el-cascader__dropdown,[role='listbox'],[role='tree'],[role='dialog'],.el-dialog,.ant-modal,[data-reka-popper-content-wrapper],[data-radix-popper-content-wrapper]");
      if (inPicker) return false;
      if (el.closest("[class*='process'], [class*='workflow'], [class*='user-select'], [class*='add-user']")) return false;
      return Boolean(el.closest("nav, .el-menu, .ant-menu, .el-menu-item, .ant-menu-item, .el-pagination, .ant-pagination"));
    }).catch(() => false);
  }

  async clickTarget(selector: string) {
    const locator = await this.locate(selector);
    const mark = `bss-click-${Date.now().toString(36)}`;
    const marked = await locator.evaluate((el, token) => {
      const isChooserHost = (node: Element) => {
        const cls = String(node.className || "");
        if (node.getAttribute("role") === "combobox" && !node.matches("input, textarea")) return true;
        if (/^(select-trigger|combobox-trigger)$/.test(node.getAttribute("data-slot") || "")) return true;
        if (/(?:^|\s)(el-select__wrapper|ant-select-selector|arco-select-view)/.test(cls)) return true;
        return /(?:^|\s)(el-select|ant-select|arco-select|n-select|el-cascader|el-date-editor|ant-picker|arco-picker)(?:\s|$)/.test(cls);
      };
      let best: Element = el;
      let node: Element | null = el.parentElement;
      for (let i = 0; i < 6 && node && node !== document.body; i += 1, node = node.parentElement) {
        if (node.matches("[class*='form-item'], [class*='form-field'], [role='group'], td, th, [class*='table-cell'], [class*='table__cell']")) break;
        if (isChooserHost(node)) {
          best = node;
          break;
        }
      }
      best.setAttribute("data-bss-click", token);
      return true;
    }, mark).catch(() => false);
    if (marked) {
      const host = this.page().locator(`[data-bss-click="${mark}"]`).filter({ visible: true });
      if (await host.count()) return host.first();
    }
    return locator;
  }

  private async hostClick(locator: Locator) {
    await locator.evaluate(el => {
      if (!(el instanceof Element)) return;
      const host = el.closest(".el-select, .ant-select, .arco-select, .n-select, .el-cascader, .el-date-editor, .ant-picker, .arco-picker, [role='combobox'], [data-slot='select-trigger'], [data-slot='combobox-trigger']")
        || (el instanceof HTMLElement ? el : el.parentElement);
      const surface = host instanceof Element
        ? host.querySelector(".el-select__wrapper, .el-input__wrapper, .ant-select-selector, .arco-select-view, .el-select__selected-item, .el-input, .ant-picker-input, [role='combobox'], [data-slot='select-trigger'], [data-slot='combobox-trigger']")
        : null;
      const target = (surface instanceof HTMLElement ? surface : host instanceof HTMLElement ? host : null);
      if (!target) return;
      for (const type of ["pointerdown", "mousedown", "pointerup", "mouseup"]) {
        target.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window }));
      }
      target.click();
    });
  }

  async clickSafely(locator: Locator, intent: "field" | "option" | "button" | "navigation" = "button", retried = false): Promise<void> {
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
      if (nav && !inPicker && clickIntent !== "navigation") return "nav";
      if (el.closest("a[href]") && !inPicker && clickIntent !== "button" && clickIntent !== "navigation") return "nav";
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
    if (kind === "occluded") {
      if (!retried && intent === "field" && await this.completeChooserDialog()) {
        return this.clickSafely(locator, intent, true);
      }
      throw new Error("Target is behind an open dialog; click a control inside the dialog or picker");
    }
    if (kind === "nav") throw new Error("Refusing to click navigation; that would leave the form and discard filled fields");
    try {
      await locator.first().click({ timeout: 1_200, noWaitAfter: true });
    } catch {
      const allowForce = await locator.first().evaluate((el, clickIntent) => {
        const box = el.getBoundingClientRect();
        if (box.width * box.height > 80_000) return false;
        if (el.matches(".el-overlay,.el-overlay-dialog,.v-modal,.ant-modal-mask,.arco-modal-mask")) return false;
        if (el.matches("a[href]") && clickIntent !== "navigation") return false;
        if (el.closest("nav, .el-menu, .ant-menu") && clickIntent !== "navigation" && !el.closest(".el-select-dropdown,[role='listbox'],.el-dialog,[role='dialog'],[class*='process'],[class*='workflow']")) return false;
        return clickIntent === "field" || clickIntent === "option" || clickIntent === "navigation"
          || el.matches("button, [role='button'], input, textarea, select, [type='submit'], [type='button']");
      }, intent).catch(() => intent !== "button");
      if (!allowForce) throw new Error("Click failed; not forcing a page click that can navigate away");
      await locator.first().click({ force: true, timeout: 800, noWaitAfter: true });
    }
  }

  private async dropdownScope() {
    const page = this.page();
    const dropdowns = page.locator(DROPDOWNS);
    const count = await dropdowns.count();
    let best: Locator | undefined;
    let bestScore = -1;
    for (let index = 0; index < count; index += 1) {
      const dropdown = dropdowns.nth(index);
      const options = await dropdown.locator(OPTION_ITEMS).filter({ visible: true }).count();
      if (!options) continue;
      const score = await dropdown.evaluate(el => {
        const style = getComputedStyle(el);
        const z = Number(style.zIndex) || 0;
        const pos = style.position === "fixed" || style.position === "absolute" ? 1000 : 0;
        return pos + z + Math.min(el.getBoundingClientRect().height, 400);
      }).catch(() => 0);
      if (score >= bestScore) {
        bestScore = score;
        best = dropdown;
      }
    }
    if (best) return best;
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

  async pickCalendarDay(dayText: string, isoDate?: string) {
    if (!(await this.hasDatePanel())) throw new Error("Refusing to click a bare day number on the page; open the date field first");
    const page = this.page();
    const panel = this.datePanel().last();
    if (isoDate) {
      const dated = panel.locator(`[title="${isoDate}"]:not(.ant-picker-cell-disabled), [data-date="${isoDate}"], [aria-label="${isoDate}"]`).filter({ visible: true }).last();
      if (await dated.count()) {
        await this.clickSafely(dated, "option");
        return;
      }
    }
    const day = String(Number(dayText));
    const exact = panel.getByRole("gridcell", { name: day, exact: true })
      .or(panel.locator("[role='gridcell'], .el-date-table-cell__text, .el-date-table-cell, td.available .cell, td.available, .ant-picker-cell-inner")
        .filter({ hasText: exactText(day) }))
      .last();
    if (await exact.count()) {
      await this.clickSafely(exact, "option");
      return;
    }
    const any = panel.locator(".el-date-table-cell__text, td.available .cell, .ant-picker-cell-inner, [role='gridcell']").filter({ visible: true }).first();
    if (!(await any.count())) throw new Error("Date panel has no selectable day");
    await this.clickSafely(any, "option");
  }

  private datePanel() {
    return this.page().locator(ACTIVE_DATE_OVERLAYS);
  }

  private async closeDatePanel() {
    const panel = this.datePanel();
    if (!(await panel.count())) return;
    const current = panel.last();
    const confirm = current.locator("button, [role='button']").filter({ hasText: /^\s*(确\s*定|确\s*认|完\s*成|应\s*用|ok|apply|done)\s*$/i }).first();
    if (await confirm.count()) await this.clickSafely(confirm, "button").catch(() => {});
    else await this.page().keyboard.press("Tab").catch(() => {});
    await current.waitFor({ state: "hidden", timeout: 500 }).catch(() => {});
  }

  async dismissTransientOverlays() {
    const page = this.page();
    try {
      await this.closeDatePanel();
      if (await this.dropdownScope()) {
        const opener = page.locator("[aria-expanded='true'], .el-select__wrapper.is-focused, .el-select.is-focused .el-select__wrapper, .el-input__wrapper.is-focus, .ant-select-open .ant-select-selector").first();
        if (await opener.count()) await this.clickSafely(opener, "field").catch(() => {});
        else await page.keyboard.press("Tab").catch(() => {});
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

  private async overlayState(): Promise<{ dialogs: number; drops: string; dates: string }> {
    return this.page().evaluate(`(() => {
      const vis = (sel) => [...document.querySelectorAll(sel)].filter((el) => {
        if (el.hidden || el.closest("[hidden]")) return false;
        const box = el.getBoundingClientRect();
        const style = getComputedStyle(el);
        return box.width > 2 && box.height > 2 && style.display !== "none" && style.visibility !== "hidden";
      });
      const keyOf = (el) => {
        const box = el.getBoundingClientRect();
        return [el.className, Math.round(box.top), Math.round(box.left), String(el.textContent || "").replace(/\\s+/g, " ").trim().slice(0, 48)].join("|");
      };
      const dialogs = vis("[role='dialog'], [role='alertdialog'], .el-dialog, .el-drawer, .ant-modal, .arco-modal")
        .filter((el) => !/picker-panel|picker-dropdown|picker__popper|el-date-picker|el-date-range-picker|el-time-panel|el-time-picker|ant-picker-dropdown|arco-picker-container|datepicker/i.test(String(el.className || "")));
      const kitDrops = vis(".el-select-dropdown, .el-select__popper, .el-cascader__dropdown, .el-autocomplete-suggestion, .ant-select-dropdown, .ant-tree-select-dropdown, .arco-select-dropdown, .arco-select-popup, .arco-tree-select-popup, .arco-cascader-popup, .arco-trigger-popup, [class*='select-popup'], [class*='tree-select-popup'], [class*='cascader-popup'], [class*='trigger-popup'], [role='listbox'], [data-reka-popper-content-wrapper], [data-radix-popper-content-wrapper], [data-state='open'][data-slot='popover-content'], [data-state='open'][data-slot='select-content'], [data-state='open'][data-slot='combobox-content'], [data-state='open'][data-slot='dropdown-menu-content']")
        .filter((el) => el.querySelector("[role='option'], [role='treeitem'], [role='listbox'], [role='tree'], .ant-select-item-option, .ant-select-tree-title, .ant-select-tree, .ant-tree-title, .el-select-dropdown__item, .el-tree-node"));
      const looseDrops = vis("ul").filter((el) => el.querySelector("[role='option'], .el-select-dropdown__item, .ant-select-item-option"));
      const drops = [...new Set([...kitDrops, ...looseDrops])];
      const dates = vis(".el-picker-panel, .el-popper.el-date-picker, .el-picker__popper, .el-date-range-picker, .el-time-panel, .ant-picker-dropdown, .arco-picker-container, [class*='picker-panel'], [class*='picker-dropdown']");
      return {
        dialogs: dialogs.length,
        drops: drops.map(keyOf).sort().join("||"),
        dates: dates.map(keyOf).sort().join("||")
      };
    })()`) as Promise<{ dialogs: number; drops: string; dates: string }>;
  }

  private overlayOpened(before: { dialogs: number; drops: string; dates: string }, after: { dialogs: number; drops: string; dates: string }) {
    return after.dialogs > before.dialogs || after.drops !== before.drops || after.dates !== before.dates;
  }

  private async chooserOpened(before: { dialogs: number; drops: string; dates: string }) {
    const deadline = Date.now() + 1_600;
    while (Date.now() < deadline) {
      if (this.overlayOpened(before, await this.overlayState())) return true;
      if (await this.dropdownScope()) return true;
      await this.page().waitForTimeout(80);
    }
    return this.overlayOpened(before, await this.overlayState()) || Boolean(await this.dropdownScope());
  }

  private async activateChooser(selector: string) {
    await this.completeChooserDialog();
    await this.dismissTransientOverlays();
    const target = await this.clickTarget(selector);
    const well = target.locator("button, [role='button'], [class*='avatar'], [class*='plus'], [class*='add']").first();
    const clickable = (await well.count()) ? well : target;
    const before = await this.overlayState();
    await this.clickSafely(clickable, "field").catch(() => this.hostClick(clickable));
    if (await this.chooserOpened(before)) return { before, opened: true };
    const retry = await this.overlayState();
    await this.hostClick(clickable);
    return { before: retry, opened: await this.chooserOpened(retry) };
  }

  private async nativeSelect(target: Locator) {
    const own = target.evaluate(el => el instanceof HTMLSelectElement).catch(() => false);
    if (await own) return target;
    const inner = target.locator("select").first();
    return (await inner.count()) ? inner : undefined;
  }

  async openSelect(target: Locator) {
    if (await this.nativeSelect(target)) return target;
    await this.completeChooserDialog();
    await this.dismissTransientOverlays();
    const selfIsSurface = await target.evaluate(el => (
      !el.matches("input, textarea")
      && (el.matches(".el-select, .el-select__wrapper, .el-input__wrapper, .ant-select, .ant-select-selector, .arco-select, .arco-select-view, [role='combobox'], [data-slot='select-trigger'], [data-slot='combobox-trigger']"))
    )).catch(() => false);
    const ancestorSurface = target.locator("xpath=ancestor::*[contains(@class,'el-select__wrapper') or contains(@class,'el-input__wrapper') or contains(@class,'ant-select-selector') or contains(@class,'arco-select-view') or @role='combobox' or contains(@data-slot,'select-trigger') or contains(@data-slot,'combobox-trigger')][1]");
    const surface = target.locator(WIDGET_SURFACES);
    const nested = target.locator(".el-select__wrapper, .el-input__wrapper, .ant-select-selector, .arco-select-view, [class*='arco-select-view'], [role='combobox'], [data-slot='select-trigger'], [data-slot='combobox-trigger']");
    const clickable = selfIsSurface
      ? target
      : (await ancestorSurface.count()) ? ancestorSurface.first()
      : (await surface.count()) ? surface.first()
      : (await nested.count()) ? nested.first() : target;
    const opened = async (before: { dialogs: number; drops: string; dates: string }) => {
      if (!(await this.chooserOpened(before))) return undefined;
      return this.dropdownScope();
    };
    const beforeClick = await this.overlayState();
    await this.clickSafely(clickable, "field").catch(() => this.hostClick(clickable));
    const fromClick = await opened(beforeClick);
    if (fromClick) return fromClick;
    const beforeHost = await this.overlayState();
    await this.hostClick(target);
    const fromHost = await opened(beforeHost);
    if (fromHost) return fromHost;
    throw new Error("Select dropdown did not open");
  }

  private async dateEditorMode(dateWrap: Locator) {
    return dateWrap.evaluate(el => {
      const input = el instanceof HTMLInputElement ? el : el.querySelector("input");
      const type = input?.getAttribute("type") || "";
      const blob = `${el.className} ${input?.placeholder || ""}`;
      if (/datetime-local|^time$|^month$/i.test(type) || /datetimerange|--datetime|--time(?:\s|$)|时分|时间选择/i.test(blob)) return "datetime";
      return "date";
    }).catch(() => "date" as const);
  }

  private async alignDatePanel(isoDate: string) {
    const [year, month] = isoDate.split("-").map(Number);
    if (!year || !month) return;
    const panel = this.datePanel().last();
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
      const readonly = await dateField.target.evaluate(el => (
        el instanceof HTMLInputElement && (el.readOnly || el.hasAttribute("readonly"))
      )).catch(() => false);
      const managed = await dateField.host.evaluate(el => /ant-picker|el-date-editor|arco-picker|n-date-picker/i.test(String(el.className || ""))).catch(() => false);
      let current = await dateField.target.inputValue().catch(() => "");
      let usedRealPanel = false;
      if (managed && !current.includes(filled.slice(0, 10))) {
        if (!(await this.hasDatePanel())) await this.clickSafely(dateField.target, "field").catch(() => {});
        if (await this.hasDatePanel()) {
          usedRealPanel = true;
          await this.alignDatePanel(filled.slice(0, 10));
          await this.pickCalendarDay(String(Number(filled.slice(8, 10))), filled.slice(0, 10));
          await this.page().waitForTimeout(100);
          current = await dateField.target.inputValue().catch(() => "");
          if (!current.includes(filled.slice(0, 10)) && await this.hasDatePanel()) {
            const panel = this.datePanel().last();
            const confirm = panel.locator("button, [role='button']")
              .filter({ hasText: /^\s*(确\s*定|完\s*成|应\s*用|ok|apply|done)\s*$/i })
              .filter({ visible: true })
              .last();
            if (await confirm.count() && await confirm.isEnabled().catch(() => true)) {
              await this.clickSafely(confirm, "button");
              await this.page().waitForTimeout(120);
              current = await dateField.target.inputValue().catch(() => "");
            }
          }
          if (!current.includes(filled.slice(0, 10))) throw new Error("Date picker did not commit the selected value");
        }
      }
      if (!usedRealPanel && !current.includes(filled.slice(0, 10))) {
        if (!readonly) {
          await dateField.target.fill(filled, { timeout: 800 }).catch(async () => {
            await dateField.target.fill(filled, { force: true, timeout: 600 });
          });
        }
        await this.commitValue(dateField.target, filled);
        current = await dateField.target.inputValue().catch(() => "");
        if (!current.includes(filled.slice(0, 10))) await this.commitValue(dateField.target, filled);
      }
      const rangeCount = await dateField.host.locator("input").count();
      if (rangeCount <= 1) await this.closeDatePanel();
      else {
        const values = await dateField.host.locator("input").evaluateAll(els => els.map(el => String((el as HTMLInputElement).value || "")));
        if (values.filter(item => /\d{4}-\d{2}-\d{2}/.test(item)).length >= Math.min(2, rangeCount)) {
          await this.closeDatePanel();
        }
      }
      return;
    }
    const chooser = await locator.evaluate(el => {
      if (el instanceof HTMLTextAreaElement) return false;
      if (/^(请选择|请挑选|please select|please choose)/i.test(String(el.getAttribute("placeholder") || "").trim())) return true;
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
    const native = await this.nativeSelect(target);
    if (native) {
      for (const label of labels) await native.selectOption({ label });
      return { ok: true, url: page.url() };
    }
    for (const label of labels) {
      const scope = await this.openSelect(target);
      const option = this.optionLocator(label, scope).filter({ visible: true }).first();
      await option.waitFor({ state: "visible", timeout: 1_200 });
      if (await this.isNavigationTarget(option)) throw new Error("Refusing to click navigation; that would leave the form and discard filled fields");
      await this.clickOption(option);
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

  private async clickOption(locator: Locator) {
    const clicked = await locator.evaluate(el => {
      const target = el instanceof HTMLElement
        ? (el.closest("[role='option'], [role='treeitem'], .el-select-dropdown__item, .el-tree-node__content, .ant-select-item-option, .ant-select-tree-title, .ant-tree-title, .arco-select-option, .arco-tree-node-title, .arco-tree-node, .arco-cascader-option")
          || el.querySelector("[role='option'], [role='treeitem'], .ant-select-tree-title, .ant-tree-title, .el-tree-node__content, .arco-tree-node-title")
          || el)
        : null;
      if (!(target instanceof HTMLElement)) return false;
      for (const type of ["pointerdown", "mousedown", "pointerup", "mouseup"]) {
        target.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window }));
      }
      target.click();
      return true;
    }).catch(() => false);
    if (!clicked) await locator.click({ force: true, timeout: 800 });
  }

  async firstVisibleOption(scope?: Locator) {
    const root = scope || await this.dropdownScope();
    if (!root) throw new Error("No open select dropdown");
    const item = root.locator(OPTION_ITEMS).filter({ visible: true }).first();
    await item.waitFor({ state: "visible", timeout: 1_200 });
    const navigates = await item.evaluate(el => Boolean(el.closest("nav, aside, .el-menu, .ant-menu, a[href]")) && !el.closest(".el-select-dropdown, .ant-select-dropdown, [role='listbox'], [role='tree'], [data-reka-popper-content-wrapper], [data-radix-popper-content-wrapper]"));
    if (navigates) throw new Error("Visible option is a navigation item; not clicking it");
    return ((await item.innerText()) || "").replace(/\s+/g, " ").trim();
  }

  async chooseFirstOption(selector: string, opened?: Locator) {
    const target = await this.clickTarget(selector);
    const native = await this.nativeSelect(target);
    if (native) {
      const labels = await native.locator("option").evaluateAll(elements => elements.map(el => String(el.textContent || "").replace(/\s+/g, " ").trim()).filter(Boolean));
      const value = labels.find(item => item && !/请选择|please select|select/i.test(item)) || labels[0];
      if (!value) throw new Error("Select opened but no option became visible");
      await native.selectOption({ label: value });
      return value;
    }
    const scope = opened || await this.openSelect(target);
    const item = scope.locator(OPTION_ITEMS).filter({ visible: true }).first();
    await item.waitFor({ state: "visible", timeout: 2_000 });
    const options = await this.dropdownOptions(scope);
    const value = ((await item.innerText()) || "").replace(/\s+/g, " ").trim() || await this.firstVisibleOption(scope);
    const fieldMeta = await target.evaluate(el => {
      const attrs = ["name", "data-field", "data-name", "data-key", "data-model"];
      const generated = /^(el-id-\d+|el-[a-z]+-\d+|reka-v-[a-z0-9-]+|input-\d+|select-\d+|aria-id|:r[0-9a-z]+$)/i;
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
    }).catch(() => ({ label: "", name: undefined, scope: "page" as const })) as {
      label: string;
      name?: string;
      scope: "page" | "dialog";
    };
    await this.host.recordSelectObservation?.({
      label: fieldMeta.label,
      name: fieldMeta.name,
      scope: fieldMeta.scope,
      value,
      options
    });
    await this.clickOption(item);
    await this.page().locator(DROPDOWNS).first().waitFor({ state: "hidden", timeout: 250 }).catch(() => {});
    for (let level = 0; level < 3 && await this.dropdownScope(); level += 1) {
      const nextScope = await this.dropdownScope();
      if (!nextScope) break;
      const next = nextScope.locator(OPTION_ITEMS).filter({ visible: true }).first();
      if (!(await next.count())) break;
      await this.clickOption(next);
      await this.page().waitForTimeout(80);
    }
    await this.page().locator(DROPDOWNS).first().waitFor({ state: "hidden", timeout: 600 }).catch(() => {});
    return value;
  }

  private async captureFields(): Promise<PageSnapshot> {
    await this.revealCollapsedFilters();
    const snap = await this.page().locator("body").evaluate(SNAPSHOT_FIELDS_IN_PAGE) as PageSnapshot;
    return {
      ...snap,
      todoFields: (snap.formFields || []).filter(field => !field.skip && !field.disabled && !field.filled),
      todoCount: (snap.formFields || []).filter(field => !field.skip && !field.disabled && !field.filled).length
    };
  }

  async formScopeKey() {
    const page = this.page();
    const dialog = await this.lastFormDialog(page.mainFrame());
    if (!dialog) return `${page.url()}|page`;
    const title = await dialog.locator(".el-dialog__title, .el-drawer__title, .ant-modal-title, .arco-modal-title, [class*='dialog-title'], [class*='modal-title']")
      .filter({ visible: true }).first().innerText().catch(() => "");
    return `${page.url()}|dialog|${title.replace(/\s+/g, " ").trim()}`;
  }

  async recordFormInventory() {
    const snapshot = await this.captureFields();
    await this.host.writePageInventory(this.page(), snapshot);
    return snapshot;
  }

  async captureSnapshot(): Promise<PageSnapshot> {
    const page = this.page();
    await this.revealCollapsedFilters();
    type FrameSnapshot = PageSnapshot & { frameUrl: string; visible: boolean; unavailable?: boolean };
    const frames: FrameSnapshot[] = [];
    for (const frame of page.frames()) {
      if (frame !== page.mainFrame()) await frame.waitForLoadState("domcontentloaded").catch(() => {});
      try {
        const box = await frame.locator("body").boundingBox().catch(() => null);
        const snap = await frame.locator("body").evaluate(SNAPSHOT_IN_PAGE) as PageSnapshot;
        const visible = Boolean(box && box.width >= 20 && box.height >= 20);
        frames.push({ frameUrl: frame.url(), visible, ...snap });
      } catch {
        frames.push({ frameUrl: frame.url(), unavailable: true, visible: false, formFields: [] });
      }
    }
    const main: FrameSnapshot = frames[0] || { frameUrl: page.url(), visible: true };
    const child = frames.slice(1).filter(frame => !frame.unavailable && (frame.visible || (frame.formFields || []).length > 0));
    const childFields = child.flatMap(frame => frame.formFields || []);
    const formFields = [...(main.formFields || []), ...childFields];
    const todoFields = formFields.filter(field => !field.skip && !field.disabled && !field.filled);
    const operationInventory = [main, ...child].flatMap(frame => (frame.controls || []).flatMap(control => {
      if (control.chrome) return [];
      const actionable = control.tag === "button" || control.tag === "a" || control.role === "button" || control.role === "link" || control.type === "submit";
      if (!actionable) return [];
      const label = String(control.text || control.label || "").replace(/\s+/g, " ").trim();
      const operation = inferUiOperationIntent(label, String(frame.frameUrl || frame.url || page.url()));
      if (!operation) return [];
      return [{
        operation,
        label,
        selector: typeof control.selector === "string" ? control.selector : undefined,
        enabled: !control.disabled,
        frameUrl: typeof frame.frameUrl === "string" ? frame.frameUrl : undefined
      }];
    }));
    const availableOperations: OperationKind[] = [...new Set(operationInventory.filter(item => item.enabled).map(item => item.operation))];
    const navigationInventory = [main, ...child].flatMap(frame => frame.navigationInventory || []);
    const snapshot: PageSnapshot = {
      ...main,
      frames: frames.slice(1),
      formFields,
      todoFields,
      todoCount: todoFields.length,
      recentUserActions: this.host.recentUserActions(),
      recordedManualSteps: this.host.recordedManualSteps?.() || [],
      followManualSteps: Boolean(this.host.followManualSteps?.()),
      availableOperations,
      operationInventory,
      navigationInventory
    };
    await this.host.writePageInventory(page, snapshot);
    return snapshot;
  }

  private fieldHint(field: FormField, extra = "") {
    return `${field.label || ""} ${field.name || ""} ${field.selector || ""} ${field.error || ""} ${extra}`;
  }

  private looksNumericField(field: FormField) {
    if (field.kind === "number") return true;
    const hint = this.fieldHint(field);
    if (/编号|车牌|单号|手机号|电话|证件号|身份证/i.test(hint)) return false;
    return /显示顺序|排序|顺序|车座|座位数|数量|人数|人次|(?:件|份|台|套|次)数|天数|时长|工时|金额|价格|单价|裸车价|\bdays?\b|\bduration\b|\bhours?\b|\bsort\b|\bseat\b|\bqty\b|quantity|price|amount/i.test(hint);
  }

  private sampleValue(field: FormField, dateOffset = 0) {
    if (field.kind === "date") {
      const endLike = field.rangeIndex === 1 || dateOffset % 2 === 1;
      return `${localIsoDate(dateOffset)} ${endLike ? "23:59:59" : "00:00:00"}`;
    }
    if (this.looksNumericField(field)) return "1";
    const hint = this.fieldHint(field);
    if (/座机|区号/.test(hint)) return "0516-85881234";
    if (/联系方式|手机号|手机|电话|mobile|phone|lxfs|lxdh/i.test(hint)) return "13212341234";
    if (/邮箱|email/i.test(hint)) return "user@example.com";
    const placeholder = String(field.selector || "").match(/^placeholder=(.+)$/s)?.[1] || "";
    const text = String(placeholder || field.label || field.name || "").replace(/[：:*：\s]/g, "").slice(0, 12);
    return text ? `样例-${text}` : "样例";
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

  private async chooserDataRows(dialog: Locator) {
    const specific = dialog.locator(".vxe-body--row, .el-table__body .el-table__row, .ant-table-tbody tr.ant-table-row:not(.ant-table-measure-row):not(.ant-table-placeholder)");
    const source = await specific.count()
      ? specific
      : dialog.locator(TABLE_DATA_ROWS);
    return source
      .filter({ visible: true })
      .filter({ hasNotText: /暂无数据|没有数据|no data/i })
      .filter({ hasText: /\S/ });
  }

  private async waitForChooserChoices(dialog: Locator, timeoutMs = 2000) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      if (await (await this.chooserDataRows(dialog)).count()) return true;
      if (await dialog.locator(".el-tree-node__content, [role='treeitem']").filter({ visible: true }).count()) return true;
      await this.page().waitForTimeout(200);
    }
    return Boolean(
      await (await this.chooserDataRows(dialog)).count()
      || await dialog.locator(".el-tree-node__content, [role='treeitem']").filter({ visible: true }).count()
    );
  }

  private async tableRowByLabel(_root: Frame | Locator, name: string) {
    const wanted = name.replace(/\s+/g, "");
    if (!wanted || (wanted.length < 2 && /^[\x00-\x7F]+$/.test(wanted))) return undefined;
    const mark = `bss-row-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`;
    const found = await this.page().evaluate(`(() => {
      const wanted = ${JSON.stringify(wanted)};
      const mark = ${JSON.stringify(mark)};
      const dialogs = [...document.querySelectorAll("[role='dialog'], .el-dialog, .ant-modal, .arco-modal")].filter(function(el) {
        return el.getClientRects().length && getComputedStyle(el).visibility !== "hidden";
      });
      const root = dialogs[dialogs.length - 1] || document;
      const rows = [...root.querySelectorAll(${JSON.stringify(TABLE_DATA_ROWS)})];
      const visible = rows.filter(function(el) {
        return el.getClientRects().length && getComputedStyle(el).visibility !== "hidden";
      });
      const hit = visible.find(function(row) {
        const text = String(row.innerText || "").replace(/\\s+/g, "");
        if (text === wanted) return true;
        const cells = [...row.querySelectorAll("td, [class*='body--column'], .ant-table-cell, .el-table__cell")]
          .map(function(cell) { return String(cell.innerText || "").replace(/\\s+/g, ""); })
          .filter(Boolean);
        return cells[0] === wanted;
      });
      if (!hit) return false;
      hit.setAttribute("data-bss-row-hit", mark);
      return true;
    })()`).catch(() => false);
    if (!found) return undefined;
    const row = this.page().locator(`[data-bss-row-hit="${mark}"]`).first();
    if (!(await row.count())) return undefined;
    return row;
  }

  private async isTableDataRow(locator: Locator) {
    return Boolean(await locator.getAttribute("data-bss-row-hit").catch(() => null));
  }

  private async isChooserCheckbox(locator: Locator) {
    return locator.evaluate((el) => Boolean(
      el.closest(".vxe-cell--checkbox, .col--checkbox, .ant-checkbox, .el-checkbox")
    )).catch(() => false);
  }

  private async domActivateRow(locator: Locator) {
    const mark = await locator.getAttribute("data-bss-row-hit").catch(() => "");
    const token = mark || `bss-row-act-${Date.now().toString(36)}`;
    if (!mark) {
      const ok = await locator.evaluate((el, value) => {
        el.setAttribute("data-bss-row-hit", value);
        return true;
      }, token).catch(() => false);
      if (!ok) return false;
    }
    return Boolean(await this.page().evaluate(`(() => {
      const el = document.querySelector('[data-bss-row-hit="${token}"]');
      if (!el) return false;
      const row = el.closest(".vxe-body--row, .ant-table-tbody tr.ant-table-row, .el-table__body .el-table__row, tr") || el;
      const vxe = row.querySelector(".vxe-cell--checkbox, .col--checkbox .vxe-cell");
      if (vxe) {
        const icon = vxe.querySelector(".vxe-checkbox--icon, .vxe-checkbox--unchecked-icon, .vxe-table-icon-checkbox-unchecked") || vxe;
        icon.click();
        return true;
      }
      const box = row.querySelector("input[type=checkbox]:not([disabled]), input[type=radio]:not([disabled])");
      if (box) box.click();
      else row.click();
      return true;
    })()`).catch(() => false));
  }

  private async activateChooserRow(dialog: Locator, rowIndex = 0) {
    const mark = `bss-chooser-${Date.now().toString(36)}`;
    const marked = await dialog.evaluate((el, token) => {
      el.setAttribute("data-bss-chooser", token);
      return true;
    }, mark).catch(() => false);
    if (!marked) return false;
    return Boolean(await this.page().evaluate(`(() => {
      const root = document.querySelector('[data-bss-chooser="${mark}"]');
      if (!root) return false;
      const rows = [...root.querySelectorAll(".vxe-body--row, .ant-table-tbody tr.ant-table-row, .el-table__body .el-table__row, tbody tr")]
        .filter(function(el) {
          if (!el.getClientRects().length || getComputedStyle(el).visibility === "hidden") return false;
          return Boolean(el.querySelector(".vxe-cell--checkbox, .col--checkbox .vxe-cell, input[type=checkbox], input[type=radio], [role=checkbox], [role=radio], .el-checkbox, .ant-checkbox, .arco-checkbox, .n-checkbox"));
        });
      const row = rows[${Number(rowIndex) || 0}];
      if (!row) return false;
      const vxe = row.querySelector(".vxe-cell--checkbox, .col--checkbox .vxe-cell");
      if (vxe) {
        const icon = vxe.querySelector(".vxe-checkbox--icon, .vxe-checkbox--unchecked-icon, .vxe-table-icon-checkbox-unchecked") || vxe;
        icon.click();
        return true;
      }
      const box = row.querySelector("input[type=checkbox]:not([disabled]), input[type=radio]:not([disabled])");
      if (box) box.click();
      else {
        const customBox = row.querySelector("[role=checkbox], [role=radio], .el-checkbox, .ant-checkbox, .arco-checkbox, .n-checkbox");
        if (customBox) customBox.click();
        else row.click();
      }
      return true;
    })()`).catch(() => false));
  }

  private async pickChooserRow(dialog: Locator, rowIndex = 0) {
    await this.waitForChooserChoices(dialog);
    if (await this.activateChooserRow(dialog, rowIndex)) return;
    const tree = dialog.locator(".el-tree-node__content, [role='treeitem']").filter({ visible: true }).first();
    if (await tree.count()) {
      await this.clickSafely(tree, "option");
      await this.page().waitForTimeout(400);
      const nested = (await this.chooserDataRows(dialog)).first();
      if (await nested.count()) {
        if (!(await this.activateChooserRow(dialog, 0))) await this.clickSafely(nested, "option").catch(() => {});
        return;
      }
      return;
    }
    const row = dialog.locator(DIALOG_CHOICES).filter({ visible: true }).first();
    if (!(await row.count())) throw new Error("Picker dialog has no selectable row");
    if (!(await this.activateChooserRow(dialog, rowIndex))) await this.clickSafely(row, "option");
  }

  private async completeChooserDialog() {
    const dialog = await this.lastChooserDialog();
    if (!dialog) return false;
    await this.waitForChooserChoices(dialog);
    const confirm = () => dialog.locator("button, [role='button']").filter({ hasText: /^\s*(确\s*定|确\s*认|选\s*择|ok|confirm)\s*$/i }).first();
    const rowCount = await (await this.chooserDataRows(dialog)).count();
    const attempts = Math.max(1, Math.min(3, rowCount || 1));
    for (let index = 0; index < attempts; index += 1) {
      try {
        await this.pickChooserRow(dialog, index);
      } catch {
        return false;
      }
      const ok = confirm();
      if (await ok.count()) await this.clickSafely(ok, "button").catch(() => {});
      await dialog.waitFor({ state: "hidden", timeout: 400 }).catch(() => {});
      if (!(await this.lastChooserDialog())) {
        await this.waitForPageQuiet(300);
        return true;
      }
    }
    await this.waitForPageQuiet(300);
    return !(await this.lastChooserDialog());
  }

  private async pickFirstChoice(selector: string) {
    await this.completeChooserDialog();
    const { before } = await this.activateChooser(selector);
    const after = await this.overlayState();
    let dialog = await this.lastChooserDialog() || (after.dialogs > before.dialogs ? await this.waitForFormDialog() : undefined);
    if (!dialog) {
      if (after.drops !== before.drops || await this.dropdownScope()) {
        const opened = await this.dropdownScope();
        if (opened) return this.chooseFirstOption(selector, opened);
      }
      throw new Error("Picker did not open a dialog or list");
    }
    await this.pickChooserRow(dialog);
    const ok = dialog.locator("button, [role='button']").filter({ hasText: /^\s*(确\s*定|确\s*认|选\s*择|ok|confirm)\s*$/i }).first();
    if (await ok.count()) await this.clickSafely(ok, "button");
    await this.waitForPageQuiet(600);
    const snapshot = await this.captureFields();
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
    const placeholder = String(field.selector || "").match(/^placeholder=(.+)$/s)?.[1] || "";
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
    if (field.kind === "text" && /请输入.+(编码|名称|code|name)$/i.test(placeholder || String(field.label || ""))) {
      return [choose, pick, type];
    }
    if (field.kind === "checkbox" || field.kind === "radio") return [toggle];
    return [type];
  }

  private async liveDateValue(field: FormField) {
    const selector = field.selector || `label=${field.label}`;
    try {
      const dateField = await this.dateFieldTarget(await this.locate(selector), field.rangeIndex);
      const live = await dateField?.target.inputValue().catch(() => "") || "";
      return /\d{4}-\d{2}-\d{2}/.test(live) ? live : "";
    } catch {
      return "";
    }
  }

  private async fillOneField(field: FormField, startUrl: string, dateOffset = 0, force = false) {
    const selector = field.selector || `label=${field.label}`;
    if (this.page().url() !== startUrl) throw new Error("Page navigated; stopping so filled fields are not overwritten");
    if (field.kind === "upload" || field.skip) return { label: field.label, selector, kind: field.kind, skipped: true };
    if (!force && field.filled && (field.kind === "select" || field.kind === "picker") && !field.invalid) {
      return { label: field.label, selector, kind: field.kind, value: field.value, skipped: true };
    }
    const attempts = this.strategiesFor(field, dateOffset);
    if (!attempts.length) throw new Error(`Could not fill ${field.label}`);
    const chooser = field.kind === "select" || field.kind === "picker";
    let lastError: unknown;
    for (const attempt of attempts) {
      for (let tryIndex = 0; tryIndex < 2; tryIndex += 1) {
        try {
          if (field.kind === "select" || field.kind === "picker" || tryIndex === 0) {
            if (field.kind !== "picker") await this.completeChooserDialog();
            await this.dismissTransientOverlays();
          }
          const value = String((await attempt()) || "");
          const after = this.matchField(await this.captureFields(), field);
          const liveDate = field.kind === "date" && !after?.filled ? await this.liveDateValue(field) : "";
          if ((!after?.filled && !liveDate) || (chooser && this.isSampleValue(String(after?.value || value), field))) {
            throw new Error(`Could not fill ${field.label}`);
          }
          return { label: field.label, selector, kind: field.kind, value: after?.value || liveDate || value };
        } catch (error) {
          lastError = error;
        }
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

  private async repairBusinessFailure(reason: string, startUrl: string) {
    if (this.page().url() !== startUrl) return undefined;
    const marker = reason.match(/不能为空|未填写|必填|请选择|required/i);
    if (!marker || marker.index === undefined) return undefined;
    const prefix = reason.slice(0, marker.index);
    const subject = prefix.split(/[:：,，;；]/).at(-1)?.trim() || "";
    const normalize = (value: string) => value
      .replace(/[\s*＊()（）\[\]【】:_-]/g, "")
      .replace(/(?:编号|编码|号码|名称|ID)$/i, "")
      .toLowerCase();
    const wanted = normalize(subject);
    if (!wanted) return undefined;
    const snapshot = await this.captureFields();
    const ranked = (snapshot.formFields || []).flatMap(field => {
      if (field.skip || field.disabled) return [];
      const label = normalize(String(field.label || ""));
      const name = normalize(String(field.name || ""));
      let score = 0;
      if (label && label === wanted) score = 12;
      else if (label && (wanted.includes(label) || label.includes(wanted))) score = 10;
      else if (name && (wanted.includes(name) || name.includes(wanted))) score = 8;
      if (field.kind === "picker" || field.kind === "select") score += 3;
      return score > 0 ? [{ field, score }] : [];
    }).sort((left, right) => right.score - left.score);
    const target = ranked[0]?.field;
    if (!target) {
      const companions = await this.patchVueFormCompanions();
      return companions.length ? { label: companions.join("、"), previous: "", value: "companion" } : undefined;
    }
    const selector = target.selector || `label=${target.label}`;
    const previous = target.value;
    if (target.kind === "picker") await this.pickFirstChoice(selector);
    else if (target.kind === "select") await this.chooseFirstOption(selector);
    else if (!target.filled) await this.fillOneField(target, startUrl);
    else return undefined;
    const current = this.matchField(await this.captureFields(), target);
    if (!current?.filled || this.isSampleValue(String(current.value || ""), target)) return undefined;
    return { label: target.label, previous, value: current.value };
  }

  private async repairFormValues(startUrl: string) {
    const snapshot = await this.captureFields();
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
    const afterDates = await this.captureFields();
    const pageErrors = (afterDates.errors || []).join(" ");
    for (const field of afterDates.formFields || []) {
      if (field.disabled || field.skip) continue;
      if (this.requiredNumberInvalid(field) || (field.invalid && field.kind === "number")) {
        await this.fillField(field.selector || `label=${field.label}`, "1");
        continue;
      }
      if (!field.invalid && !field.error) continue;
      if (field.kind !== "text" && field.kind !== "textarea") continue;
      const hint = this.fieldHint(field, pageErrors);
      const next = /座机|区号/.test(hint)
        ? "0516-85881234"
        : /手机|11位|联系方式|电话/.test(hint)
          ? "13212341234"
          : undefined;
      if (next && next !== field.value) {
        await this.fillField(field.selector || `label=${field.label}`, next);
      }
    }
    void startUrl;
  }

  private async expandRepeatableRows(attempted: Set<string>) {
    // 「添加X」and row synonyms expand a form collection. List-page「新增X」opens create.
    const addRow = /^(新增一行|添加一行|加一行|添加明细|新增明细|新增行|新增子项|新增条目|新增费用项|添加.{1,16})$/;
    const genericAdd = /^\s*(添加|新增)\s*$/;
    const excluded = /附件|上传|文件|申请$|单据$|订单$|列表$/;
    const before = (await this.captureFields()).formFields || [];
    const snapshot = await this.captureSnapshot();
    const controls = (snapshot.controls || []).filter(item => {
      const name = String(item.text || item.label || "").replace(/\s+/g, "");
      return addRow.test(name)
        && !excluded.test(name)
        && (!snapshot.scope || !item.scope || item.scope === snapshot.scope);
    });
    const control = controls.find(item => {
      const name = String(item.text || item.label || "").replace(/\s+/g, "");
      const key = `${this.page().url()}|${String(item.selector || "")}|${name}`;
      return !attempted.has(key) && !this.expandedRepeatables.has(key);
    });
    let contextualAdd: Locator | undefined;
    let contextualKey = "";
    if (!control) {
      const candidates = this.page().locator("button, [role='button']").filter({ visible: true }).filter({ hasText: genericAdd });
      for (let index = 0; index < await candidates.count(); index += 1) {
        const candidate = candidates.nth(index);
        const text = String(await candidate.innerText().catch(() => "")).replace(/\s+/g, "");
        const key = `${this.page().url()}|contextual:${index}|${text}`;
        if (attempted.has(key) || this.expandedRepeatables.has(key)) continue;
        const belongsToRepeatableSection = await candidate.evaluate((element) => {
          let node = element.parentElement;
          for (let depth = 0; node && depth < 7; depth += 1, node = node.parentElement) {
            const heading = node.querySelector("h1,h2,h3,h4,h5,h6,legend,.el-card__header,.ant-card-head-title,[class*='title'],[class*='header']");
            const context = String(heading?.textContent || node.getAttribute("aria-label") || "").replace(/\s+/g, "");
            if (/明细|行项目|子项|条目|费用项|清单/.test(context) && !/附件|上传/.test(context)) return true;
          }
          return false;
        }).catch(() => false);
        if (belongsToRepeatableSection) {
          contextualAdd = candidate;
          contextualKey = key;
          break;
        }
      }
    }
    const name = String(control?.text || control?.label || await contextualAdd?.innerText().catch(() => "") || "").replace(/\s+/g, "");
    // No matching control means there is no repeatable section to expand. Do
    // not manufacture a dialog fallback: that used to count every pass as a
    // failed retry even though no add-row control existed on the page.
    if (!control && !contextualAdd) return { attempted: false, expanded: false, name: "" };
    const key = control
      ? `${this.page().url()}|${String(control.selector || "")}|${name}`
      : contextualKey || `${this.page().url()}|dialog|${name}`;
    attempted.add(key);
    try {
      if (control?.selector) await this.click(String(control.selector));
      else if (contextualAdd) await this.clickSafely(contextualAdd, "button");
      else return { attempted: false, expanded: false, name: "" };
    } catch {
      if (!name) return { attempted: true, expanded: false, name };
      await this.page().locator(`${DIALOGS} button, ${DIALOGS} [role='button']`).filter({ hasText: name }).first()
        .click({ timeout: 1_200 }).catch(() => {});
    }
    await this.waitForPageQuiet(600);
    if (await this.lastChooserDialog()) await this.completeChooserDialog();
    const after = (await this.captureFields()).formFields || [];
    const expanded = after.some(field => !before.some(item => item.selector === field.selector && item.label === field.label));
    if (expanded) this.expandedRepeatables.add(key);
    return { attempted: true, expanded, name };
  }

  private submitControl(snapshot: PageSnapshot) {
    const scope = snapshot.scope;
    const controls = (snapshot.controls || []).filter(control => !scope || !control.scope || control.scope === scope);
    const scored = controls.flatMap(control => {
      if (control.chrome) return [];
      const text = String(control.text || control.label || "").replace(/\s+/g, "");
      if (!text || CANCEL_LABEL.test(text)) return [];
      const button = control.tag === "button" || control.role === "button" || control.type === "submit";
      if (!button) return [];
      if (!(control.type === "submit" || SUBMIT_LABEL.test(text))) return [];
      const draft = /草稿|draft/i.test(text);
      const write = WRITE_SUBMIT_LABEL.test(text) && !draft;
      const search = /^(搜索|查询|search)$/i.test(text);
      return [{
        selector: /^(label|placeholder)=/i.test(String(control.selector || "")) ? `text=${text}` : String(control.selector || `text=${text}`),
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

  private async revealCollapsedFilters() {
    const roots = ".search-form, .ant-pro-table-search, .el-form--inline, .filter-container, .table-search, .vxe-grid--form-wrapper, [class*='search-form'], [class*='table-search'], [class*='filter-bar'], [class*='search-bar'], [class*='filter-form'], [class*='query-form']";
    const expand = /^(展开|展开筛选|高级搜索|高级|更多筛选|Expand|Advanced)$/i;
    let changed = false;
    for (const frame of this.page().frames()) {
      const buttons = frame.locator(roots).locator("button, a, [role='button'], .el-button, .ant-btn").filter({ visible: true });
      const count = await buttons.count().catch(() => 0);
      for (let index = 0; index < count; index += 1) {
        const button = buttons.nth(index);
        const state = await button.evaluate(el => ({
          text: String(el.textContent || "").replace(/\s+/g, " ").trim(),
          expanded: el.getAttribute("aria-expanded"),
          handled: el.getAttribute("data-bss-filter-expanded") === "true"
        })).catch(() => undefined);
        if (!state || state.handled || state.expanded === "true" || !expand.test(state.text.replace(/\s+/g, ""))) continue;
        await button.evaluate(el => el.setAttribute("data-bss-filter-expanded", "true")).catch(() => {});
        try {
          await this.clickSafely(button, "button");
          changed = true;
        } catch {
          await button.evaluate(el => el.removeAttribute("data-bss-filter-expanded")).catch(() => {});
        }
      }
    }
    if (changed) await this.waitForPageQuiet(800);
    return changed;
  }

  async exerciseForm(allFields = false, isCancelled: () => boolean = () => false) {
    await this.dismissTransientOverlays();
    await this.completeChooserDialog();
    const filled: Array<Record<string, unknown>> = [];
    const failed: Array<Record<string, unknown>> = [];
    const failedKeys = new Set<string>();
    const startUrl = this.page().url();
    let dateOffset = 0;
    const fieldKey = (field: FormField) => `${field.scope || ""}|${field.label}|${field.selector || ""}|${field.groupIndex ?? field.rangeIndex ?? 0}`;
    const cancelledResult = (snapshot?: PageSnapshot) => ({
      ok: false,
      cancelled: true,
      reason: "自动浏览器操作已终止。",
      filled,
      failed,
      errors: snapshot?.errors || [],
      todoFields: snapshot?.todoFields || [],
      todoCount: snapshot?.todoCount ?? (snapshot?.todoFields || []).length,
      formFields: snapshot?.formFields || [],
      recordedManualSteps: snapshot?.recordedManualSteps || [],
      followManualSteps: false
    });
    if (isCancelled()) return cancelledResult();
    const run = async (fields: FormField[], force = false) => {
      for (const field of fields) {
        if (isCancelled()) return false;
        const key = fieldKey(field);
        if (failedKeys.has(key)) continue;
        const offset = field.kind === "date" ? dateOffset++ : 0;
        try {
          const result = await this.fillOneField(field, startUrl, offset, force);
          if (!result.skipped) filled.push(result);
        } catch (error: any) {
          failedKeys.add(key);
          failed.push({ label: field.label, selector: field.selector || `label=${field.label}`, kind: field.kind, requiredInteraction: force, error: String(error?.message || error) });
        }
        if (isCancelled()) return false;
      }
      return true;
    };
    let after = await this.captureSnapshot();
    if (isCancelled()) return cancelledResult(after);
    let fullPassPending = allFields;
    const recent = this.host.recentUserActions() as Array<{ eventType?: string; label?: string; name?: string; selector?: string }>;
    const alreadyOperated = (field: FormField) => recent.some(action =>
      (action.eventType === "input" || action.eventType === "change")
      && ((field.selector && action.selector === field.selector) || (field.name && action.name === field.name) || (field.label && action.label === field.label))
    );
    const attemptedRepeatables = new Set<string>();
    const expansionFailures: Array<Record<string, unknown>> = [];
    const maxPasses = Math.max(6, Math.min(32, (after.controls || []).length + 6));
    for (let pass = 0; pass < maxPasses && this.page().url() === startUrl; pass += 1) {
      if (isCancelled()) return cancelledResult(after);
      const expansion = await this.expandRepeatableRows(attemptedRepeatables);
      if (isCancelled()) return cancelledResult(after);
      if (expansion.attempted) {
        if (!expansion.expanded) expansionFailures.push({ label: expansion.name || "业务明细", error: "点击添加控件后没有出现可填写字段" });
        after = await this.captureFields();
      }
      const force = fullPassPending;
      const candidates = force ? (after.formFields || []).filter(field => !alreadyOperated(field)) : (after.todoFields || []);
      fullPassPending = false;
      const leftover = candidates.filter(field => !field.skip && !field.disabled && !failedKeys.has(fieldKey(field)));
      if (!leftover.length) {
        if (expansion.attempted) continue;
        if (await this.revealHiddenSections()) {
          after = await this.captureFields();
          continue;
        }
        break;
      }
      if (!await run(leftover.filter(field => field.kind === "select"), force)) return cancelledResult(after);
      await this.waitForPageQuiet(800);
      if (isCancelled()) return cancelledResult(after);
      if (!await run(leftover.filter(field => field.kind === "date"), force)) return cancelledResult(after);
      await this.closeDatePanel();
      if (isCancelled()) return cancelledResult(after);
      if (!await run(leftover.filter(field => field.kind !== "select" && field.kind !== "date" && field.kind !== "picker"), force)) return cancelledResult(after);
      if (!await run(leftover.filter(field => field.kind === "picker"), force)) return cancelledResult(after);
      await this.completeChooserDialog();
      await this.dismissTransientOverlays();
      if (isCancelled()) return cancelledResult(after);
      after = await this.captureFields();
    }
    if (isCancelled()) return cancelledResult(after);
    if (!this.formReady(after, startUrl)) {
      if (await this.revealHiddenSections()) {
        if (isCancelled()) return cancelledResult(after);
        after = await this.captureFields();
        if (!await run((after.todoFields || []).filter(field => !field.skip && !field.disabled && !failedKeys.has(fieldKey(field))))) return cancelledResult(after);
        after = await this.captureFields();
      }
    }
    if (isCancelled()) return cancelledResult(after);
    await this.repairFormValues(startUrl);
    if (isCancelled()) return cancelledResult(after);
    after = await this.captureSnapshot();
    const leftoverFailed = failed.filter(item => item.requiredInteraction ||
      (after.formFields || []).some(field => field.label === item.label && !field.filled && !field.skip && !field.disabled)
    );
    const ok = this.formReady(after, startUrl) && leftoverFailed.length === 0 && expansionFailures.length === 0;
    return {
      ok,
      scope: after.scope,
      filled,
      failed: [...leftoverFailed, ...expansionFailures],
      errors: after.errors || [],
      todoFields: after.todoFields || [],
      todoCount: after.todoCount ?? (after.todoFields || []).length,
      formFields: after.formFields || [],
      recordedManualSteps: after.recordedManualSteps || [],
      followManualSteps: Boolean(this.host.followManualSteps?.())
    };
  }

  private async patchVueFormCompanions() {
    try {
      const changed = await this.page().evaluate(PATCH_VUE_FORM_COMPANIONS) as unknown;
      return Array.isArray(changed) ? changed.map(item => String(item)).filter(Boolean) : [];
    } catch {
      return [];
    }
  }

  async submitForm(stage = 0): Promise<Record<string, unknown>> {
    await this.completeChooserDialog();
    await this.dismissTransientOverlays();
    let before = await this.captureSnapshot();
    const startUrl = this.page().url();
    const button = this.submitControl(before);
    if (!button) throw new Error("No submit/search button in the active form");
    const write = WRITE_SUBMIT_LABEL.test(button.text) && !/搜索|查询|search/i.test(button.text);
    let repaired = false;
    if (write) {
      const companions = await this.patchVueFormCompanions();
      if (companions.length) repaired = true;
    }
    if (!this.formReady(before, startUrl)) {
      await this.repairFormValues(startUrl);
      before = await this.captureSnapshot();
      repaired = true;
    }
    if (!this.formReady(before, startUrl) && WRITE_SUBMIT_LABEL.test(button.text) && !/搜索|查询|search/i.test(button.text)) {
      return {
        ok: false,
        submitted: button.text,
        repaired: false,
        sawRequest: false,
        errors: before.errors || [],
        url: this.page().url(),
        scope: before.scope,
        todoFields: before.todoFields || [],
        todoCount: before.todoCount ?? (before.todoFields || []).length,
        formFields: before.formFields || [],
        recordedManualSteps: before.recordedManualSteps || [],
        followManualSteps: true
      };
    }
    const pending = this.awaitFormRequest(write ? 6_000 : 3_000, write);
    await this.click(button.selector);
    const responseSignal = pending.then(response => ({ kind: "response" as const, response }));
    const dialogSignal = before.scope === "dialog" ? new Promise<never>(() => {}) : this.page().locator(DIALOGS).first()
      .waitFor({ state: "visible", timeout: 1_200 })
      .then(() => ({ kind: "dialog" as const }))
      .catch(() => new Promise<never>(() => {}));
    const outcome = write ? await Promise.race([responseSignal, dialogSignal]) : await responseSignal;
    const formResponse = outcome.kind === "response" ? outcome.response : undefined;
    const sawRequest = Boolean(formResponse);
    const businessFailure = formResponse ? businessResponseFailureReason(formResponse) : undefined;
    const authenticationFailure = formResponse ? authenticationFailureReason(formResponse) : undefined;
    await this.waitForPageQuiet();
    const after = await this.captureSnapshot();
    if (write && !sawRequest && after.scope === "dialog" && before.scope !== "dialog" && stage < 2) {
      const dialogFields = await this.exerciseForm();
      if (!dialogFields.ok) return { ...dialogFields, submitted: button.text, submitStages: stage + 1 };
      const continued = await this.submitForm(stage + 1);
      return {
        ...continued,
        repaired: repaired || continued.repaired === true,
        submitStages: stage + 2
      };
    }
    const leftoverErrors = [...(after.errors || []), ...(businessFailure ? [businessFailure] : [])];
    const closed = this.page().url() !== startUrl || after.scope !== before.scope;
    const invalid = (after.formFields || []).some(field => field.invalid || this.requiredNumberInvalid(field));
    const leftoverTodos = (after.todoFields || []).filter(field => !field.skip && !field.disabled);
    const ok = write
      ? Boolean(sawRequest && !businessFailure && leftoverErrors.length === 0 && (closed || (!invalid && leftoverTodos.length === 0)))
      : closed || Boolean(sawRequest && leftoverErrors.length === 0 && !invalid && leftoverTodos.length === 0);
    const businessRepair = businessFailure && !authenticationFailure
      ? await this.repairBusinessFailure(businessFailure, startUrl).catch(() => undefined)
      : undefined;
    if (write && businessRepair && stage < 2) {
      const retried = await this.submitForm(stage + 1);
      return {
        ...retried,
        repaired: true,
        businessRepair,
        submitStages: Math.max(Number(retried.submitStages || 0), stage + 2)
      };
    }
    return {
      ok,
      submitted: button.text,
      repaired,
      sawRequest,
      response: formResponse ? { status: formResponse.status, url: formResponse.url, method: formResponse.method } : undefined,
      businessFailure,
      loginRequired: Boolean(authenticationFailure),
      loginReason: authenticationFailure,
      businessRepair,
      retryReady: Boolean(businessRepair),
      submitStages: stage + 1,
      errors: leftoverErrors,
      url: this.page().url(),
      scope: after.scope,
      todoFields: after.todoFields || [],
      todoCount: after.todoCount ?? (after.todoFields || []).length,
      formFields: after.formFields || [],
      recordedManualSteps: after.recordedManualSteps || [],
      followManualSteps: !ok
    };
  }
}
