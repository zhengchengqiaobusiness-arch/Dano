import { useEffect, useMemo, useState } from "react";
import {
  Drawer, Button, Checkbox, Input, InputNumber, DatePicker, Radio, Typography, Tag,
  Alert, Descriptions, Space, message, Image,
  Select, Switch,
} from "antd";
import { invokeSkill, listFieldOptions, SkillManifest, TaskOutcome, JSONSchema, SkillFieldCallMetadata } from "../api/skills";

const STATE_COLOR: Record<string, string> = {
  completed: "success", failed: "error", rejected: "error",
  cancelled: "warning", needs_input: "warning", needs_select: "warning",
};

// 候选项标识:优先 id,否则第一个值(与后端 _candidate_id 一致)
function candidateId(c: Record<string, unknown>): unknown {
  return c.id ?? Object.values(c)[0];
}
function candidateLabel(c: Record<string, unknown>, tmpl?: string): string {
  if (tmpl) return tmpl.replace(/\{(\w+)\}/g, (_, k) => String(c[k] ?? ""));
  return JSON.stringify(c);
}

type EnumSelectOption = { label: string; selectValue: string; value: unknown; disabled?: boolean };

// schema 不完整时保留语义兜底:日期/数字/文本
const isDate = (s: string) => /date|time|日期|时间|起止|开始|结束|起|止/i.test(s);
const isNum = (s: string) => /days|num|count|amount|qty|天数|数量|金额|时长|个数/i.test(s);

function isEmptyValue(v: unknown): boolean {
  return v === "" || v == null || (Array.isArray(v) && v.length === 0);
}

function initialFormValues(p: JSONSchema): Record<string, unknown> {
  const values: Record<string, unknown> = {};
  Object.entries(p?.properties || {}).forEach(([key, prop]) => {
    if (prop.type === "boolean") values[key] = false;
    else if (prop.type === "array" || prop.type === "list-enum") values[key] = [];
  });
  return values;
}

function jsonSkeleton(p: JSONSchema): string {
  const o: Record<string, unknown> = {};
  for (const [k, prop] of Object.entries(p?.properties || {})) {
    if (prop.type === "boolean") o[k] = false;
    else if (prop.type === "array" || prop.type === "list-enum") o[k] = [];
    else o[k] = "";
  }
  return JSON.stringify(o, null, 2);
}

function hasOwn(obj: Record<string, unknown>, key: string): boolean {
  return Object.prototype.hasOwnProperty.call(obj, key);
}

function selectKey(v: unknown): string {
  return `${typeof v}:${JSON.stringify(v)}`;
}

function enumOptions(p: JSONSchema): EnumSelectOption[] {
  const item = p.items || {};
  const valueMap = { ...(item["x-enum-value-map"] || {}), ...(p["x-enum-value-map"] || {}) };
  const rawOptions = [...(item["x-enum-options"] || []), ...(p["x-enum-options"] || [])];
  const enumValues = [...(item.enum || []), ...(p.enum || [])];
  const out: EnumSelectOption[] = [];
  const seen = new Set<string>();
  const push = (label: string, value: unknown, disabled?: boolean) => {
    const key = selectKey(value);
    if (seen.has(key)) return;
    seen.add(key);
    out.push({ label, selectValue: key, value, disabled });
  };

  rawOptions.forEach((opt) => {
    if (opt && typeof opt === "object") {
      const label = String(opt.label ?? opt.name ?? opt.value ?? "");
      if (!label) return;
      const mapped = hasOwn(valueMap, label) ? valueMap[label] : opt.value;
      push(label, mapped ?? label, !!opt.disabled);
      return;
    }
    const label = String(opt);
    push(label, hasOwn(valueMap, label) ? valueMap[label] : opt);
  });
  enumValues.forEach((v) => push(String(v), v));
  return out;
}

function schemaWithCallMetadata(p: JSONSchema, meta?: SkillFieldCallMetadata): JSONSchema {
  if (!meta) return p;
  const out: JSONSchema = { ...p };
  const hasEnumMeta = !!(
    meta.type === "enum" ||
    meta.type === "list-enum" ||
    meta.enum_options?.length ||
    meta.enum_value_map ||
    meta.options_source
  );
  if (hasEnumMeta) {
    out.type = meta.type === "list-enum" ? "array" : "string";
    if (!out.format) out.format = "name-ref";
  } else if (!out.type && meta.type) {
    out.type = meta.type;
  }
  if (!out.format && meta.format) out.format = meta.format;
  if (!out["x-enum-options"] && meta.enum_options) out["x-enum-options"] = meta.enum_options;
  if (!out["x-enum-value-map"] && meta.enum_value_map) out["x-enum-value-map"] = meta.enum_value_map;
  if (!out["x-options-source"] && meta.options_source) out["x-options-source"] = true;
  return out;
}

export default function InvokeDrawer({ skill, onClose }: { skill: SkillManifest | null; onClose: () => void }) {
  const [mode, setMode] = useState<"form" | "json">("form");
  const [values, setValues] = useState<Record<string, unknown>>({});
  const [text, setText] = useState("{}");
  const [confirm, setConfirm] = useState(false);
  const [running, setRunning] = useState(false);
  const [out, setOut] = useState<TaskOutcome | null>(null);
  const [lastInput, setLastInput] = useState<Record<string, unknown>>({});  // 供消歧选中后带同一组输入重调
  const [liveOptions, setLiveOptions] = useState<Record<string, EnumSelectOption[]>>({});
  const [loadingOptions, setLoadingOptions] = useState<Record<string, boolean>>({});

  const props = useMemo(() => skill?.parameters?.properties || {}, [skill]);
  const required = useMemo(() => new Set(skill?.parameters?.required || []), [skill]);

  useEffect(() => {
    if (skill) {
      setValues(initialFormValues(skill.parameters));
      setText(jsonSkeleton(skill.parameters));
      setConfirm(skill.requires_confirmation);
      setMode("form");
      setOut(null);
      setLiveOptions({});
      setLoadingOptions({});
    }
  }, [skill]);

  const setVal = (k: string, v: unknown) => setValues((p) => ({ ...p, [k]: v }));

  async function doInvoke(input: Record<string, unknown>) {
    if (!skill) return;
    setRunning(true);
    setOut(null);
    setLastInput(input);
    try {
      setOut(await invokeSkill(skill.name, input, confirm));
    } catch (e: any) {
      message.error("调用失败:" + (e?.response?.data?.detail || e.message));
    } finally {
      setRunning(false);
    }
  }

  async function run() {
    if (!skill) return;
    let input: Record<string, unknown>;
    if (mode === "json") {
      try {
        input = JSON.parse(text || "{}");
      } catch (e: any) {
        message.error("输入不是合法 JSON:" + e.message);
        return;
      }
    } else {
      const missing = [...required].filter((k) => isEmptyValue(values[k]));
      if (missing.length) {
        message.error("缺必填:" + missing.join(", "));
        return;
      }
      // 丢掉空的可选字段;选择/数字/日期/布尔已是调用所需类型
      input = Object.fromEntries(Object.entries(values).filter(([, v]) => !isEmptyValue(v)));
    }
    await doInvoke(input);
  }

  async function loadLiveOptions(field: string) {
    if (!skill || liveOptions[field]?.length || loadingOptions[field]) return;
    setLoadingOptions((p) => ({ ...p, [field]: true }));
    try {
      const r = await listFieldOptions(skill.name.replace(/\./g, "__"), field);
      const opts: EnumSelectOption[] = (r.options || []).map((opt) => {
        if (opt && typeof opt === "object") {
          const label = String(opt.label ?? opt.name ?? opt.value ?? "");
          const value = opt.value ?? label;
          return { label, value, selectValue: selectKey(value), disabled: !!opt.disabled };
        }
        return { label: String(opt), value: opt, selectValue: selectKey(opt) };
      }).filter((opt) => opt.label);
      setLiveOptions((p) => ({ ...p, [field]: opts }));
      if (!opts.length) message.warning(`${field} 当前没有可选项${r.note ? `:${r.note}` : ""}`);
    } catch (e: any) {
      message.error(`拉取 ${field} 可选项失败:` + (e?.response?.data?.detail || e.message));
    } finally {
      setLoadingOptions((p) => ({ ...p, [field]: false }));
    }
  }

  const fieldRow = (key: string, rawProp: JSONSchema) => {
    const p = schemaWithCallMetadata(rawProp, skill?.call_metadata?.fields?.[key]);
    const label = p.description || key;
    const hint = `${key} ${label}`;
    const reqMark = required.has(key) ? <span style={{ color: "#cf1322" }}> *</span> : null;
    const type = (p.type || "").toLowerCase();
    const staticOptions = enumOptions(p);
    const hasLiveSource = !!p["x-options-source"];
    const options = liveOptions[key]?.length ? liveOptions[key] : staticOptions;
    const bySelectValue = new Map(options.map((opt) => [opt.selectValue, opt.value]));
    let widget;
    if (options.length || hasLiveSource) {
      const isMulti = type === "array" || type === "list-enum" || !!p.items?.enum?.length || !!p.items?.["x-enum-options"]?.length;
      if (isMulti) {
        const current = Array.isArray(values[key]) ? values[key] as unknown[] : [];
        widget = (
          <Select
            mode="multiple"
            style={{ width: "100%" }}
            allowClear
            showSearch
            loading={!!loadingOptions[key]}
            value={current.map(selectKey)}
            options={options.map((opt) => ({ label: opt.label, value: opt.selectValue, disabled: opt.disabled }))}
            placeholder={hasLiveSource && !options.length ? "打开下拉拉取真实选项" : key}
            notFoundContent={loadingOptions[key] ? "正在拉取真实选项..." : "暂无选项"}
            onDropdownVisibleChange={(open) => { if (open && hasLiveSource) loadLiveOptions(key); }}
            onFocus={() => { if (hasLiveSource) loadLiveOptions(key); }}
            onChange={(selected) => setVal(key, selected.map((v) => bySelectValue.get(v)))}
          />
        );
      } else {
        widget = (
          <Select
            style={{ width: "100%" }}
            allowClear={!required.has(key)}
            showSearch
            loading={!!loadingOptions[key]}
            value={!isEmptyValue(values[key]) ? selectKey(values[key]) : undefined}
            options={options.map((opt) => ({ label: opt.label, value: opt.selectValue, disabled: opt.disabled }))}
            placeholder={hasLiveSource && !options.length ? "打开下拉拉取真实选项" : key}
            notFoundContent={loadingOptions[key] ? "正在拉取真实选项..." : "暂无选项"}
            onDropdownVisibleChange={(open) => { if (open && hasLiveSource) loadLiveOptions(key); }}
            onFocus={() => { if (hasLiveSource) loadLiveOptions(key); }}
            onChange={(selected) => setVal(key, selected == null ? undefined : bySelectValue.get(selected))}
          />
        );
      }
    } else if (type === "boolean") {
      widget = (
        <Switch
          checked={!!values[key]}
          checkedChildren="是"
          unCheckedChildren="否"
          onChange={(v) => setVal(key, v)}
        />
      );
    } else if (type === "number" || type === "integer" || ((!type || type === "string") && isNum(hint))) {
      widget = <InputNumber style={{ width: "100%" }} value={values[key] as number}
                            onChange={(v) => setVal(key, v)} />;
    } else if (type === "date" || type === "datetime" || ((type === "string" || !type) && (p.format === "date" || p.format === "date-time" || isDate(hint)))) {
      widget = <DatePicker showTime={type === "datetime" || p.format === "date-time"} style={{ width: "100%" }} onChange={(_, ds) => setVal(key, ds)} />;
    } else {
      widget = <Input value={(values[key] as string) ?? ""} onChange={(e) => setVal(key, e.target.value)}
                      placeholder={key} />;
    }
    return (
      <div key={key} style={{ marginBottom: 12 }}>
        <div style={{ marginBottom: 4, fontSize: 13 }}>
          {label}{reqMark}{label !== key && <Typography.Text type="secondary" style={{ fontSize: 12 }}> · {key}</Typography.Text>}
        </div>
        {widget}
      </div>
    );
  };

  const keys = Object.keys(props);

  return (
    <Drawer title={skill ? `测试调用 · ${skill.name}` : ""} width={560} open={!!skill} onClose={onClose} destroyOnClose>
      {skill && (
        <>
          <Descriptions size="small" column={1} style={{ marginBottom: 12 }}>
            <Descriptions.Item label="风险">
              <Tag color={skill.risk_level >= "L3" ? "orange" : "default"}>{skill.risk_level}</Tag>
              {skill.requires_confirmation && <Tag color="orange">写操作需确认</Tag>}
            </Descriptions.Item>
            {(skill.recording_mode || skill.verification_status || skill.verification_basis) && (
              <Descriptions.Item label="录制验证">
                {skill.recording_mode && (
                  <Tag color={skill.recording_mode === "real_submit" ? "green" : "blue"}>
                    {skill.recording_mode === "real_submit" ? "真实提交录制" : skill.recording_mode === "intercepted_submit" ? "只录制不提交" : skill.recording_mode}
                  </Tag>
                )}
                {skill.verification_status && <Tag>{skill.verification_status}</Tag>}
                {skill.verification_basis && <Tag color="default">{skill.verification_basis}</Tag>}
              </Descriptions.Item>
            )}
            <Descriptions.Item label="必填字段">{[...required].length ? [...required].join(", ") : "无"}</Descriptions.Item>
          </Descriptions>

          <Radio.Group value={mode} onChange={(e) => setMode(e.target.value)} size="small" style={{ marginBottom: 12 }}>
            <Radio.Button value="form">逐字段填写</Radio.Button>
            <Radio.Button value="json">原始 JSON</Radio.Button>
          </Radio.Group>

          {mode === "form" ? (
            <>
              <Typography.Text type="secondary" style={{ display: "block", marginBottom: 8 }}>
                业务字段(__base_url__ / 模板 / 凭证后端注入,无需填)
              </Typography.Text>
              {keys.length ? keys.map((k) => fieldRow(k, props[k])) : <Typography.Text type="secondary">该 skill 无参数</Typography.Text>}
            </>
          ) : (
            <>
              <Typography.Text type="secondary">input(业务字段 JSON)</Typography.Text>
              <Input.TextArea value={text} onChange={(e) => setText(e.target.value)} autoSize={{ minRows: 8, maxRows: 18 }}
                              style={{ fontFamily: "monospace", marginTop: 60 }} />
            </>
          )}

          <Space style={{ marginTop: 12 }}>
            <Checkbox checked={confirm} onChange={(e) => setConfirm(e.target.checked)}>confirm(L3 写操作必须勾)</Checkbox>
            <Button type="primary" loading={running} onClick={run}>调用</Button>
          </Space>

          {out && (
            <div style={{ marginTop: 16 }}>
              <Alert
                type={(STATE_COLOR[out.state] as any) || "info"}
                showIcon
                message={<span>state: <b>{out.state}</b></span>}
                description={out.message}
              />
              {out.state === "needs_select" && (() => {
                const sel = ((out.audit as any)?.select || {}) as { bind?: string; candidates?: Record<string, unknown>[]; label_template?: string };
                const cands = sel.candidates || [];
                return (
                  <div style={{ marginTop: 12 }}>
                    <Typography.Text>请选择一个候选(将以 <b>{sel.bind}</b> 带同一组输入重新调用):</Typography.Text>
                    <Space wrap style={{ marginTop: 8 }}>
                      {cands.map((c, i) => (
                        <Button key={i} loading={running}
                                onClick={() => doInvoke({ ...lastInput, [sel.bind as string]: candidateId(c) })}>
                          {candidateLabel(c, sel.label_template)}
                        </Button>
                      ))}
                    </Space>
                  </div>
                );
              })()}
              <Typography.Text type="secondary" style={{ display: "block", marginTop: 12 }}>返回(structured_output)</Typography.Text>
              <Input.TextArea
                readOnly
                value={JSON.stringify(out.exec_result?.structured_output ?? null, null, 2)}
                autoSize={{ minRows: 4, maxRows: 14 }}
                style={{ fontFamily: "monospace", marginTop: 6 }}
              />
              {(() => {
                const shots = (((out.exec_result as any)?.evidence?.screenshots) || []) as string[];
                const imgs = shots.filter((s) => typeof s === "string" && s.startsWith("data:image"));
                if (!imgs.length) return null;
                return (
                  <div style={{ marginTop: 12 }}>
                    <Typography.Text type="secondary" style={{ display: "block", marginBottom: 6 }}>
                      页面执行截图({imgs.length})· 点击放大
                    </Typography.Text>
                    <Image.PreviewGroup>
                      <Space wrap>
                        {imgs.map((src, i) => (
                          <Image key={i} src={src} width={130}
                                 style={{ border: "1px solid #f0f0f0", borderRadius: 4 }} />
                        ))}
                      </Space>
                    </Image.PreviewGroup>
                  </div>
                );
              })()}
              {out.audit && (out.audit as any).fact_check && (
                <Alert style={{ marginTop: 10 }} type="info" message="事实核查证据" description={<pre style={{ margin: 0, fontSize: 12 }}>{JSON.stringify((out.audit as any).fact_check, null, 2)}</pre>} />
              )}
            </div>
          )}
        </>
      )}
    </Drawer>
  );
}
