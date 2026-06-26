import { useEffect, useMemo, useState } from "react";
import {
  Drawer, Button, Checkbox, Input, InputNumber, DatePicker, Radio, Select, Typography, Tag,
  Alert, Descriptions, Space, message, Image,
} from "antd";
import {
  invokeSkill, listSkillOptions, SkillManifest, TaskOutcome, JSONSchema, JSONSchemaProperty, ToolOption,
} from "../api/skills";

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

// 按字段名/描述猜控件类型(manifest 目前统一 type=string,故靠语义猜:日期/数字/文本)
const isDate = (s: string) => /date|time|日期|时间|起止|开始|结束|起|止/i.test(s);
const isNum = (s: string) => /days|num|count|amount|qty|天数|数量|金额|时长|个数/i.test(s);
const isSelectProp = (p: JSONSchemaProperty) =>
  p?.format === "name-ref" || p?.["x-options-source"] || Array.isArray(p?.["x-options"]) || Array.isArray(p?.enum);
const isMultiSelectProp = (p: JSONSchemaProperty) => p?.type === "array" || p?.format === "name-ref-list";

function normalizeOptions(p: JSONSchemaProperty): ToolOption[] {
  const raw = (Array.isArray(p?.["x-options"]) && p["x-options"]!.length ? p["x-options"] : p?.enum) || [];
  const out: ToolOption[] = [];
  const seen = new Set<string>();
  for (const item of raw) {
    const rec = item as Record<string, unknown>;
    const label = typeof item === "object" && item !== null && "label" in rec ? String(rec.label ?? "") : String(item ?? "");
    const value = typeof item === "object" && item !== null && "value" in rec ? String(rec.value ?? "") : String(item ?? "");
    if (!label) continue;
    const key = `${label}\u0000${value}`;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push({ label, value });
  }
  return out;
}

function jsonSkeleton(p: JSONSchema): string {
  const o: Record<string, unknown> = {};
  for (const k of Object.keys(p?.properties || {})) o[k] = "";
  return JSON.stringify(o, null, 2);
}

export default function InvokeDrawer({ skill, onClose }: { skill: SkillManifest | null; onClose: () => void }) {
  const [mode, setMode] = useState<"form" | "json">("form");
  const [values, setValues] = useState<Record<string, unknown>>({});
  const [text, setText] = useState("{}");
  const [confirm, setConfirm] = useState(false);
  const [running, setRunning] = useState(false);
  const [out, setOut] = useState<TaskOutcome | null>(null);
  const [lastInput, setLastInput] = useState<Record<string, unknown>>({});  // 供消歧选中后带同一组输入重调
  const [optionCache, setOptionCache] = useState<Record<string, ToolOption[]>>({});
  const [optionLoading, setOptionLoading] = useState<Record<string, boolean>>({});

  const props = useMemo(() => skill?.parameters?.properties || {}, [skill]);
  const required = useMemo(() => new Set(skill?.parameters?.required || []), [skill]);

  useEffect(() => {
    if (skill) {
      setValues({});
      setText(jsonSkeleton(skill.parameters));
      setConfirm(skill.requires_confirmation);
      setMode("form");
      setOut(null);
      setOptionCache({});
      setOptionLoading({});
    }
  }, [skill]);

  const setVal = (k: string, v: unknown) => setValues((p) => ({ ...p, [k]: v }));

  async function loadOptions(key: string, p: JSONSchemaProperty) {
    if (!skill || optionCache[key] || optionLoading[key]) return;
    const fallback = normalizeOptions(p);
    if (!p?.["x-options-source"]) {
      setOptionCache((prev) => ({ ...prev, [key]: fallback }));
      return;
    }
    setOptionLoading((prev) => ({ ...prev, [key]: true }));
    try {
      const res = await listSkillOptions(skill.name, key);
      setOptionCache((prev) => ({ ...prev, [key]: (res.options || []).length ? res.options : fallback }));
      if (res.note && !(res.options || []).length) message.warning(res.note);
    } catch (e: any) {
      setOptionCache((prev) => ({ ...prev, [key]: fallback }));
      message.error(`拉取 ${key} 候选失败:` + (e?.response?.data?.detail || e.message));
    } finally {
      setOptionLoading((prev) => ({ ...prev, [key]: false }));
    }
  }

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
      const missing = [...required].filter((k) => values[k] === "" || values[k] == null);
      if (missing.length) {
        message.error("缺必填:" + missing.join(", "));
        return;
      }
      // 丢掉空的可选字段;数字/日期已是正确类型
      input = Object.fromEntries(Object.entries(values).filter(([, v]) => v !== "" && v != null));
    }
    await doInvoke(input);
  }

  const fieldRow = (key: string, p: JSONSchemaProperty) => {
    const label = p.description || key;
    const hint = `${key} ${label}`;
    const reqMark = required.has(key) ? <span style={{ color: "#cf1322" }}> *</span> : null;
    let widget;
    if (isSelectProp(p)) {
      const options = optionCache[key] || normalizeOptions(p);
      const multi = isMultiSelectProp(p);
      widget = (
        <Select
          mode={multi ? "multiple" : undefined}
          showSearch
          allowClear
          optionFilterProp="label"
          style={{ width: "100%" }}
          value={(values[key] as any) ?? undefined}
          options={options}
          loading={!!optionLoading[key]}
          placeholder={key}
          onFocus={() => loadOptions(key, p)}
          onDropdownVisibleChange={(open) => { if (open) loadOptions(key, p); }}
          onChange={(v) => setVal(key, v)}
        />
      );
    } else if (isDate(hint)) {
      widget = <DatePicker style={{ width: "100%" }} onChange={(_, ds) => setVal(key, ds)} />;
    } else if (isNum(hint)) {
      widget = <InputNumber style={{ width: "100%" }} value={values[key] as number}
                            onChange={(v) => setVal(key, v)} />;
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
                              style={{ fontFamily: "monospace", marginTop: 6 }} />
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
