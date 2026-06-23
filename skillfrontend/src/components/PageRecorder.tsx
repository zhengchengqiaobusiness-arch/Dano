import { useEffect, useRef, useState } from "react";
import { Card, Form, Input, Button, Space, Typography, Alert, Tag, List, Checkbox, Collapse, Switch, message } from "antd";
import { useNavigate } from "react-router-dom";

// 方式B:网页内录制。连 WebSocket → 后端托管浏览器,画面投到这里,点击/键盘回传,实时显示捕获的步骤。
// 客户全程免安装、免命令行。

interface RecStep { op: string; locator?: string; field?: string; value?: string }
interface RecReq { method: string; url: string; has_body?: boolean; json?: boolean }
// 提交请求体拍平后的一个叶子字段(给用户勾选哪些是参数)
interface RecField { path: string; key: string; value: string; suggest_param: boolean; suggest_name: string }
// 候选写请求(抓到多个时让用户手选用哪个)
interface RecCand { idx: number; method: string; path: string }
interface RecResult {
  ok?: boolean; action?: string; risk_level?: string; mode?: string; reason?: string;
  api?: { method?: string; path?: string; params?: string[] };
}

const KEYMAP: Record<string, string> = {
  Enter: "Enter", Backspace: "Backspace", Tab: "Tab", Delete: "Delete",
  ArrowLeft: "ArrowLeft", ArrowRight: "ArrowRight", ArrowUp: "ArrowUp", ArrowDown: "ArrowDown",
};

export default function PageRecorder({ tenant, subsystem, baseUrl, storageState }: {
  tenant: string; subsystem: string; baseUrl: string; storageState: string;
}) {
  const nav = useNavigate();
  const wsRef = useRef<WebSocket | null>(null);
  const imgRef = useRef<HTMLImageElement | null>(null);
  const kbRef = useRef<HTMLInputElement | null>(null);   // 隐藏输入框:接键入(含中文 IME)并回传
  const [phase, setPhase] = useState<"idle" | "recording" | "publishing" | "done">("idle");
  const [startUrl, setStartUrl] = useState("");
  const [frame, setFrame] = useState<string>("");
  const [steps, setSteps] = useState<RecStep[]>([]);
  const [reqs, setReqs] = useState<RecReq[]>([]);   // 诊断:抓到的写请求
  const [fields, setFields] = useState<RecField[]>([]);   // 提交请求体的字段表(供勾选成参数)
  const [picked, setPicked] = useState<Record<string, { on: boolean; name: string }>>({});  // path → {勾选, 参数名}
  const [reqMeta, setReqMeta] = useState<{ method: string; url: string } | null>(null);
  const [cands, setCands] = useState<RecCand[]>([]);   // 候选写请求(可手选用哪个)
  const [chosenIdx, setChosenIdx] = useState(0);
  const [action, setAction] = useState("submit_form");
  const [title, setTitle] = useState("");
  const [result, setResult] = useState<RecResult | null>(null);
  const [intercept, setIntercept] = useState(true);   // 拦截提交:抓到请求但不真发,录制不产生真实记录
  const [err, setErr] = useState("");

  useEffect(() => () => { wsRef.current?.close(); }, []);   // 卸载时断开

  function send(obj: unknown) {
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(obj));
  }

  function start() {
    if (!tenant) { message.error("请先到「创建 / 进入租户」"); return; }
    if (!startUrl.trim()) { message.error("请填页面地址 start_url"); return; }
    setErr(""); setResult(null); setSteps([]); setReqs([]); setFrame(""); setFields([]); setPicked({}); setCands([]);
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/onboarding/page/record`);
    wsRef.current = ws;
    ws.onopen = () => send({
      type: "start", tenant, subsystem, start_url: startUrl.trim(),
      base_url: baseUrl.trim() || undefined,
      storage_state: storageState.trim() || undefined,
      intercept,   // 是否拦截提交(不产生真实记录)
    });
    ws.onmessage = (ev) => {
      let m: any; try { m = JSON.parse(ev.data); } catch { return; }
      if (m.type === "started") setPhase("recording");
      else if (m.type === "frame") setFrame(m.data);
      else if (m.type === "step") setSteps((s) => {
        const st = m.step;
        // 同一字段连续 fill/select(逐字符记)→ 覆盖上一条,实时列表一字段只显示一行最新值
        const last = s[s.length - 1];
        if (last && last.locator === st.locator && (st.op === "fill" || st.op === "select")) {
          return [...s.slice(0, -1), st];
        }
        return [...s, st];
      });
      else if (m.type === "request") setReqs((r) => [...r, m.request].slice(-40));   // 抓到的写请求(诊断)
      else if (m.type === "request_fields") {   // 抓到提交请求 → 列出请求体字段,让用户勾哪些是参数
        const fs: RecField[] = m.fields || [];
        setFields(fs);
        const pk: Record<string, { on: boolean; name: string }> = {};
        fs.forEach((f) => { pk[f.path] = { on: !!f.suggest_param, name: f.suggest_name || f.key }; });
        setPicked(pk);
        setReqMeta({ method: m.method, url: m.url });
        setCands(m.candidates || []);
        setChosenIdx(m.chosen_idx ?? 0);
        setPhase("recording");
        message.success("抓到提交请求!勾选要让 agent 传值的字段 → 确认发布");
      }
      else if (m.type === "result") {   // 留在录制现场:不关浏览器、不重来
        setResult(m.report); setPhase("recording");
        if (m.report?.ok) { setFields([]); setPicked({}); setCands([]); }   // 发布成功 → 收起字段表
      }
      else if (m.type === "error") { setErr(m.detail || "录制出错"); setPhase("idle"); }
    };
    ws.onerror = () => setErr("WebSocket 连接失败(后端是否启动、是否支持 ws 代理?)");
    ws.onclose = () => { if (phase === "recording") setPhase("idle"); };
  }

  function onImgClick(e: React.MouseEvent<HTMLImageElement>) {
    const img = imgRef.current; if (!img) return;
    const r = img.getBoundingClientRect();
    send({ type: "input", event: { kind: "click", nx: (e.clientX - r.left) / r.width, ny: (e.clientY - r.top) / r.height } });
    kbRef.current?.focus();   // 点完画面把焦点交给隐藏输入框,接下来打字(含中文)才有人接
  }
  // 隐藏输入框接键入。中文 IME 合成中(isComposing)先不传,等 compositionend 整段传;英文走 onInput。
  function relayKb(el: HTMLInputElement) {
    const v = el.value;
    if (v) { send({ type: "input", event: { kind: "text", text: v } }); el.value = ""; }
  }
  function onKbInput(e: React.FormEvent<HTMLInputElement>) {
    if ((e.nativeEvent as { isComposing?: boolean }).isComposing) return;
    relayKb(e.currentTarget);
  }
  function onKbCompositionEnd(e: React.CompositionEvent<HTMLInputElement>) {
    relayKb(e.currentTarget);
  }
  function onKbKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (KEYMAP[e.key]) { send({ type: "input", event: { kind: "key", key: KEYMAP[e.key] } }); e.preventDefault(); }
    // 可打印字符交给 onInput(IME 安全),这里只处理 Enter/Backspace/方向键等
  }

  function resetFromHere() {
    send({ type: "reset" });
    setSteps([]); setResult(null);
    message.success("已清空,从现在起只录业务步骤(登录步骤已丢弃)");
  }
  function delStep(i: number) { setSteps((s) => s.filter((_, k) => k !== i)); }   // 删掉某一步(噪声/重复/误操作)
  function patchStep(i: number, p: Partial<RecStep>) { setSteps((s) => s.map((x, k) => (k === i ? { ...x, ...p } : x))); }
  function finalize() {
    if (!action.trim()) { message.error("请填 Skill 动作名(英文)"); return; }
    if (!steps.length && !reqs.length) { message.error("还没抓到提交请求、也没录到步骤;在画面里填表并点「提交」"); return; }
    setResult(null); setPhase("publishing");
    // 后端优先用抓到的提交请求:回 request_fields 让你勾字段;没抓到才走 DOM 步骤直接发布
    send({ type: "finalize", action: action.trim(), title: title.trim(),
           success_marker: null, steps });
  }
  function chooseRequest(idx: number) {   // 抓到多个写请求时,手选用哪个建 Skill
    setChosenIdx(idx);
    send({ type: "choose_request", idx });
  }
  function toggleField(path: string, on: boolean) {
    setPicked((p) => ({ ...p, [path]: { ...p[path], on } }));
  }
  function renameField(path: string, name: string) {
    setPicked((p) => ({ ...p, [path]: { ...p[path], name } }));
  }
  function publishRequest() {
    if (!action.trim()) { message.error("请填 Skill 动作名(英文)"); return; }
    const param_map: Record<string, string> = {};
    fields.forEach((f) => { const p = picked[f.path]; if (p?.on && p.name.trim()) param_map[f.path] = p.name.trim(); });
    if (!Object.keys(param_map).length) { message.error("至少勾选一个字段作为参数"); return; }
    setResult(null); setPhase("publishing");
    send({ type: "publish_request", action: action.trim(), title: title.trim(), param_map });
  }
  function stopAll() {
    send({ type: "stop" }); wsRef.current?.close();
    setPhase("idle"); setResult(null); setSteps([]); setFrame(""); setFields([]); setPicked({}); setCands([]);
  }

  return (
    <Card size="small" title="网页录制 → 抓提交请求 → 选字段建 Skill">
      {phase === "idle" && (
        <>
          {/* <Alert
            style={{ marginBottom: 12 }} type="info" showIcon
            message="三步:① 在画面里登录并填一遍表 → ② 点表单的「提交」(系统抓下这一下发出的请求)→ ③ 在弹出的字段表里勾选哪些当参数,确认发布。"
            description="登录态自动复用、密码不记录;无需手填 base_url / 登录态。"
          /> */}
          <Form.Item label="业务页地址 start_url" required style={{ marginBottom: 12 }}>
            <Input value={startUrl} onChange={(e) => setStartUrl(e.target.value)}
                   placeholder="https://oa.example.com/reimburse/new" onPressEnter={start} />
          </Form.Item>
          
          <div><Button type="primary" onClick={start}>开始录制</Button>   <Space style={{ marginBottom: 12 }} align="center">
            <Switch checked={intercept} onChange={setIntercept} />
            <Typography.Text>拦截提交 </Typography.Text>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              开:点提交只用来抓请求,系统拦下不真发(推荐)。关:会真的提交一次。
            </Typography.Text>
          </Space>  </div>
          {err && <Alert style={{ marginTop: 12 }} type="error" showIcon message={err} />}
        </>
      )}

      {(phase === "recording" || phase === "publishing") && (
        <div>
          <Space style={{ marginBottom: 8 }} wrap>
            <Tag color="processing">{phase === "publishing" ? "发布中…" : "录制中"}</Tag>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>在画面里操作;点击/键盘会传到浏览器</Typography.Text>
            <Button size="small" disabled={phase === "publishing"} onClick={resetFromHere}>从这里开始录(登录后点)</Button>
          </Space>
          <div style={{ border: "1px solid #d9d9d9", borderRadius: 6, overflow: "hidden", lineHeight: 0, position: "relative" }}>
            {frame
              ? <img ref={imgRef} src={`data:image/jpeg;base64,${frame}`} onClick={onImgClick} draggable={false}
                     onWheel={(e) => send({ type: "input", event: { kind: "scroll", dy: e.deltaY } })}
                     style={{ width: "100%", display: "block", cursor: "crosshair" }} alt="录制画面" />
              : <div style={{ padding: 40, textAlign: "center", color: "#999", lineHeight: 1.6 }}>等待浏览器画面…(若停在登录页,直接在画面里登录即可)</div>}
            {/* 隐藏输入框:点画面里的输入框后,在这里接你的键入(支持中文),整段回传到浏览器 */}
            <input ref={kbRef} onInput={onKbInput} onKeyDown={onKbKeyDown} onCompositionEnd={onKbCompositionEnd}
                   autoComplete="off" aria-hidden="true"
                   style={{ position: "absolute", left: 0, top: 0, width: 1, height: 1, opacity: 0, border: 0, padding: 0 }} />
          </div>
          <Alert type="info" showIcon style={{ marginTop: 8, marginBottom: 4 }}
            message={<span>正常填表即可:日期、下拉<b>随便点选</b>;我们抓的是你点「提交」那一下发出的<b>整条请求</b>里的最终值,不靠记录每次点击。</span>}
            description={intercept
              ? "已开启「拦截提交」:点提交只用来抓请求,不会产生真实记录。"
              : "未开启拦截:点提交会真的提交一次(产生一条真实记录)。"} />

          {/* ★ 主路径:抓到提交请求 → 勾字段建 Skill */}
          {fields.length > 0 && (
            <Card type="inner" size="small" style={{ marginTop: 12, borderColor: "#52c41a" }}
              title={<Space wrap size={4}>
                <Tag color="success">✅ 抓到提交请求</Tag>
                {reqMeta && <Typography.Text code style={{ fontSize: 12 }}>
                  {reqMeta.method} {reqMeta.url.replace(/^https?:\/\/[^/]+/, "")}</Typography.Text>}
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>勾选要让 agent 传值的字段</Typography.Text>
              </Space>}>
              <Alert type="info" showIcon style={{ marginBottom: 8 }}
                message="勾上的字段 → 变成参数(agent 调用时按需传值);没勾的(内部 ID、流程号、表单类型等)原样提交。已自动勾选像「填写内容」的字段,请核对增减。" />
              {cands.length > 1 && (
                <div style={{ marginBottom: 8 }}>
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    抓到 {cands.length} 个写请求,当前用第 {chosenIdx + 1} 个;不是这个就点别的:</Typography.Text>
                  <div style={{ marginTop: 4 }}>
                    <Space size={4} wrap>
                      {cands.map((c) => (
                        <Tag key={c.idx} color={c.idx === chosenIdx ? "blue" : "default"}
                             style={{ cursor: "pointer" }} onClick={() => chooseRequest(c.idx)}>
                          {c.method} {c.path}
                        </Tag>
                      ))}
                    </Space>
                  </div>
                </div>
              )}
              <List
                size="small" style={{ maxHeight: 300, overflow: "auto" }}
                dataSource={fields}
                renderItem={(f) => {
                  const p = picked[f.path] || { on: false, name: f.key };
                  return (
                    <List.Item style={{ paddingLeft: 0, paddingRight: 0 }}>
                      <Space size={8} wrap>
                        <Checkbox checked={p.on} onChange={(e) => toggleField(f.path, e.target.checked)}>参数</Checkbox>
                        <Typography.Text code style={{ fontSize: 12 }}>{f.path}</Typography.Text>
                        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                          值={f.value === "" ? "(空)" : (f.value.length > 30 ? f.value.slice(0, 30) + "…" : f.value)}</Typography.Text>
                        {p.on && <>
                          <Typography.Text type="secondary" style={{ fontSize: 12 }}>参数名</Typography.Text>
                          <Input size="small" value={p.name} placeholder="参数名(英文/拼音)"
                                 onChange={(e) => renameField(f.path, e.target.value)} style={{ width: 150 }} />
                        </>}
                      </Space>
                    </List.Item>
                  );
                }}
              />
              <Space style={{ marginTop: 10 }} wrap>
                <Form.Item label="动作名" required style={{ marginBottom: 0 }}>
                  <Input value={action} onChange={(e) => setAction(e.target.value)} placeholder="submit_leave" style={{ width: 180 }} />
                </Form.Item>
                <Form.Item label="标题" style={{ marginBottom: 0 }}>
                  <Input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="提交请假" style={{ width: 160 }} />
                </Form.Item>
                <Button type="primary" loading={phase === "publishing"} onClick={publishRequest}>
                  确认发布(用勾选的字段建 Skill)
                </Button>
              </Space>
            </Card>
          )}

          {/* 还没抓到请求时:动作名 + 抓取按钮 */}
          {!fields.length && (
            <>
              <Space size="large" wrap style={{ marginTop: 12 }}>
                <Form.Item label="Skill 动作名(英文)" required style={{ marginBottom: 0 }}>
                  <Input value={action} onChange={(e) => setAction(e.target.value)} placeholder="submit_leave" style={{ width: 200 }} />
                </Form.Item>
                <Form.Item label="标题(中文)" style={{ marginBottom: 0 }}>
                  <Input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="提交请假" style={{ width: 180 }} />
                </Form.Item>
              </Space>
              <Space style={{ marginTop: 12 }} wrap>
                <Button type="primary" loading={phase === "publishing"} disabled={!steps.length && !reqs.length} onClick={finalize}>
                  {result && !result.ok ? "改完重新发布" : "停止并发布(抓提交请求)"}
                </Button>
                <Button onClick={stopAll} disabled={phase === "publishing"}>结束录制</Button>
              </Space>
              <Typography.Text type="secondary" style={{ fontSize: 12, display: "block", marginTop: 6 }}>
                在画面里填好表、点过「提交」后按这个 → 弹出字段勾选表,选好字段再确认发布。
              </Typography.Text>
            </>
          )}
          {fields.length > 0 && (
            <Space style={{ marginTop: 12 }} wrap>
              <Button loading={phase === "publishing"} onClick={finalize}>重新抓取提交请求</Button>
              <Button onClick={stopAll} disabled={phase === "publishing"}>结束录制</Button>
            </Space>
          )}

          {/* 调试区(一般不用看):DOM 步骤 + 抓到的写请求 */}
          <Collapse ghost style={{ marginTop: 8 }} items={[{
            key: "dbg",
            label: <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              调试:DOM 步骤({steps.length}) / 抓到的写请求({reqs.length})—— 一般不用看</Typography.Text>,
            children: (
              <>
                <List
                  size="small" style={{ maxHeight: 160, overflow: "auto" }}
                  header={<Typography.Text type="secondary" style={{ fontSize: 12 }}>抓到的写请求(点提交后出现带 JSON 的 = 抓到了)</Typography.Text>}
                  dataSource={reqs}
                  locale={{ emptyText: "还没抓到写请求" }}
                  renderItem={(r) => (
                    <List.Item>
                      <Space size={6} wrap>
                        <Tag color={r.json ? "green" : r.has_body ? "gold" : "default"}>{r.method}</Tag>
                        {r.json ? <Tag color="green">JSON</Tag> : r.has_body ? <Tag color="gold">非JSON</Tag> : <Tag>无body</Tag>}
                        <Typography.Text style={{ fontSize: 12 }}>{r.url.replace(/^https?:\/\/[^/]+/, "")}</Typography.Text>
                      </Space>
                    </List.Item>
                  )}
                />
                <List
                  size="small" style={{ marginTop: 8, maxHeight: 160, overflow: "auto" }}
                  header={<Typography.Text type="secondary" style={{ fontSize: 12 }}>DOM 步骤(仅没抓到请求时作兜底回放,可删)</Typography.Text>}
                  dataSource={steps}
                  locale={{ emptyText: "无" }}
                  renderItem={(s, i) => {
                    const isField = s.op === "fill" || s.op === "select" || s.op === "upload" || s.op === "pick";
                    return (
                      <List.Item actions={[<a key="d" style={{ color: "#cf1322" }} onClick={() => delStep(i)}>删除</a>]}>
                        <Space size={6} wrap>
                          <Tag color={s.op === "submit" ? "orange" : isField ? "green" : "blue"}>{i + 1}. {s.op}</Tag>
                          <Typography.Text code style={{ fontSize: 12 }}>{s.locator}</Typography.Text>
                          {isField && <Input size="small" value={s.field || ""} placeholder="参数名"
                                 onChange={(e) => patchStep(i, { field: e.target.value })} style={{ width: 120 }} />}
                        </Space>
                      </List.Item>
                    );
                  }}
                />
              </>
            ),
          }]} />

          {result && (
            <Alert
              style={{ marginTop: 12 }} type={result.ok ? "success" : "error"} showIcon
              message={result.ok ? `已发布:${result.action}` : `未发布(${result.reason || "见原因"})`}
              description={
                <Space direction="vertical" size={2}>
                  {result.ok && result.api
                    ? <span>抓到接口 <Typography.Text code>{result.api.method} {result.api.path}</Typography.Text> ·
                        参数 [{(result.api.params || []).join(", ")}] —— agent 可传这些值调用。</span>
                    : result.ok
                      ? <span>风险 {result.risk_level} · 回放 {result.mode} —— 浏览器还开着,可继续录下一个或结束。</span>
                      : <span>删掉不对的步骤(或调整),再点「改完重新发布」。浏览器没关,现场还在。</span>}
                  {result.ok && <Button type="primary" size="small" style={{ marginTop: 4 }} onClick={() => nav("/skills")}>去 Skill 目录调用</Button>}
                </Space>
              }
            />
          )}
        </div>
      )}
    </Card>
  );
}
