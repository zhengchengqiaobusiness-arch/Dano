/**
 * PI 是唯一语义决策者；旧录制逻辑绝不启动。
 *
 * 只把当前页看得见的筛选/表单/表格控件投影成事实。
 * 不判断能力、不补字段、不改名。
 */

function compact(value) {
  return String(value || "").replace(/\s+/g, " ").trim().slice(0, 80);
}

export function projectVisibleControlSnapshot(event) {
  const payload = event?.payload && typeof event.payload === "object" ? event.payload : {};
  const controls = Array.isArray(payload.controls) ? payload.controls : [];
  return {
    seq: Number(event?.seq) || 0,
    kind: "visible_control",
    url: String(payload.url || ""),
    reason: String(payload.reason || ""),
    count: controls.length,
    controls: controls.map((item) => {
      const row = item && typeof item === "object" ? item : {};
      return {
        region: String(row.region || ""),
        name: String(row.name || ""),
        label: String(row.label || ""),
        placeholder: String(row.placeholder || ""),
        control_kind: String(row.control_kind || ""),
        required_mark: Boolean(row.required_mark),
        readonly: Boolean(row.readonly),
        disabled: Boolean(row.disabled),
      };
    }),
  };
}

export function collectVisibleControlsInPage() {
  const textOf = (node) => String(node?.innerText || node?.textContent || "").replace(/\s+/g, " ").trim().slice(0, 80);
  const visible = (node) => {
    if (!node || !node.getBoundingClientRect) return false;
    const box = node.getBoundingClientRect();
    return box.width >= 2 && box.height >= 2;
  };
  const inFilter = (node) => Boolean(node.closest?.(
    ".search-form, .ant-pro-table-search, .el-form--inline, .filter-container, .table-search, [class*='search-form'], [class*='table-search'], [class*='filter-bar']",
  ));
  const seen = new Set();
  const out = [];
  const push = (row) => {
    const key = [row.region, row.name, row.label, row.placeholder, row.control_kind].join("|");
    if (seen.has(key)) return;
    if (!row.label && !row.name && !row.placeholder) return;
    seen.add(key);
    out.push(row);
  };

  const markRequired = (item, label) => (
    Boolean(
      item.classList?.contains("is-required")
      || item.querySelector?.(".el-form-item__label.is-required, .ant-form-item-required, .required"),
    )
    || /\*/.test(label)
  );

  for (const item of document.querySelectorAll(".el-form-item, .ant-form-item, form label")) {
    if (!visible(item)) continue;
    const label = textOf(item.querySelector(".el-form-item__label, .ant-form-item-label"))
      || textOf(item.matches?.("label") ? item : item.querySelector("label"));
    const input = item.querySelector("input, textarea, select");
    const isDate = Boolean(item.querySelector(".el-date-editor, .ant-picker, input[type='date'], input[type='datetime-local']"));
    const isSelect = Boolean(item.querySelector(".el-select, .ant-select, select"));
    const isUpload = Boolean(item.querySelector("input[type='file'], .el-upload, .ant-upload"))
      || /上传|附件|选择文件/.test(label);
    const isTextarea = Boolean(item.querySelector("textarea"));
    const readonly = Boolean(
      input?.readOnly
      || input?.disabled
      || item.querySelector(".is-disabled, [disabled], .el-input.is-disabled"),
    );
    let controlKind = "input";
    if (isUpload) controlKind = "upload";
    else if (isDate) controlKind = "date";
    else if (isSelect) controlKind = "select";
    else if (isTextarea) controlKind = "textarea";
    else if (readonly && !input) controlKind = "readonly";
    push({
      region: inFilter(item) ? "filter" : "form",
      name: String(input?.name || input?.id || ""),
      label,
      placeholder: String(input?.placeholder || ""),
      control_kind: controlKind,
      required_mark: markRequired(item, label),
      readonly,
      disabled: Boolean(input?.disabled),
    });
  }

  for (const input of document.querySelectorAll(".el-table input, .el-table textarea, .ant-table input, .ant-table textarea, table input, table textarea")) {
    if (!visible(input)) continue;
    const cell = input.closest("td, th");
    const row = cell?.parentElement;
    const index = row ? [...row.children].indexOf(cell) : -1;
    const table = input.closest("table, .el-table, .ant-table");
    const head = index >= 0
      ? table?.querySelector?.(`thead th:nth-child(${index + 1}), thead td:nth-child(${index + 1})`)
      : null;
    push({
      region: "table",
      name: String(input.name || input.id || ""),
      label: textOf(head),
      placeholder: String(input.placeholder || ""),
      control_kind: input.tagName === "TEXTAREA" ? "textarea" : "input",
      required_mark: false,
      readonly: Boolean(input.readOnly || input.disabled),
      disabled: Boolean(input.disabled),
    });
  }

  return out.slice(0, 80);
}

export function summarizeVisibleControls(controls) {
  return (Array.isArray(controls) ? controls : [])
    .map((item) => compact(item?.label || item?.placeholder || item?.name))
    .filter(Boolean)
    .slice(0, 24)
    .join("、");
}
