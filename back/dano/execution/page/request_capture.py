"""方式B 升级:抓"提交请求" → 参数化成可调用的内部接口(无 DOM 回放,框架无关)。

无 API 页面其实是 SPA:点提交时网页向它自己后端发了个写请求(带表单值的 JSON)。把那个请求抓下来,
请求体里**等于用户填的值**的字段 → 变成参数;内部 ID/token 等保持常量。回放就是直接发这个请求。
不依赖控件长相,比录 DOM 点击稳得多。

本模块是纯函数(不碰浏览器),便于离线测试。
"""

from __future__ import annotations

import datetime as _dt
import json
import re as _re

_WRITE = {"POST", "PUT", "PATCH", "DELETE"}
# 提交无关的高频路径(登录/校验码/字典/心跳等),挑提交请求时排除
_NOISE = ("/login", "/captcha", "/getInfo", "/dict/", "/heartbeat", "/refresh", "/upload",
          "/sse", "/socket", "/ws", ".png", ".jpg", ".css", ".js")


# 浏览器通用头(回放交给 httpx / storageState 处理,不照搬);其余应用自定义头(鉴权/租户)要带上
_DROP_HEADERS = {
    "host", "connection", "content-length", "accept", "accept-encoding", "accept-language",
    "user-agent", "referer", "origin", "cookie", "content-type", "cache-control", "pragma",
    "dnt", "upgrade-insecure-requests", "priority", "te", "if-none-match", "if-modified-since",
}


def extract_auth_headers(headers: dict | None) -> dict:
    """从录到的请求头里留下「应用自定义头」(Authorization / Admin-Token / satoken / clientid / 租户号…),
    丢掉浏览器通用头。回放时原样带上 → 不管系统用哪个 header / token key 鉴权都通用,不写死。"""
    out: dict[str, str] = {}
    for k, v in (headers or {}).items():
        kl = (k or "").lower()
        if not v or kl in _DROP_HEADERS or kl.startswith("sec-"):
            continue
        out[k] = v
    return out


def _parse_body(post_data: str | None):
    if not post_data:
        return None
    try:
        return json.loads(post_data)
    except Exception:  # noqa: BLE001 —— 非 JSON(form-urlencoded 等)暂不处理
        return None


def _values(node) -> list[str]:
    """递归取 body 里所有标量值(字符串化),用于和用户样例匹配。"""
    out: list[str] = []
    if isinstance(node, dict):
        for v in node.values():
            out += _values(v)
    elif isinstance(node, list):
        for v in node:
            out += _values(v)
    elif node is not None and not isinstance(node, bool):
        out.append(str(node))
    return out


def json_write_requests(requests: list[dict]) -> list[dict]:
    """抓到的请求里所有「带 JSON body 的写请求」(候选提交请求),保序。供前端列出来手选用哪个。"""
    out: list[dict] = []
    for r in requests:
        if (r.get("method") or "").upper() in _WRITE and _parse_body(r.get("post_data")) is not None:
            out.append(r)
    return out


# 读响应里"候选列表"的常见包装键(若依/通用):rows/records/list/data/content/items/result
_LIST_KEYS = ("rows", "records", "list", "data", "content", "items", "result", "results")


def as_list_payload(data):
    """从读响应里取出"候选列表"(下拉/选人源):data 本身是非空数组,或常见包装键里的非空数组。无则 None。

    通用:不认任何系统专属结构,只按常见列表包装键挖一到两层。供 Q2「选领导」等 select 解析。
    """
    if isinstance(data, list):
        return data or None
    if isinstance(data, dict):
        for k in _LIST_KEYS:
            v = data.get(k)
            if isinstance(v, list) and v:
                return v
            if isinstance(v, dict):                     # 再下一层(如 data.records / data.rows)
                for k2 in _LIST_KEYS:
                    v2 = v.get(k2)
                    if isinstance(v2, list) and v2:
                        return v2
    return None


def _leaf_paths(body) -> list[tuple]:
    """body 拍平成 [(点路径, 值字符串, 原始值)]。"""
    out: list[tuple] = []

    def walk(node, path):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, f"{path}.{k}" if path else k)
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")
        else:
            out.append((path, "" if node is None else str(node), node))

    walk(body, "")
    return out


def _find_value_path(node, value, prefix: str = "") -> str | None:
    """在 node 里找一个叶子值 == value 的点路径(深度优先,返回第一个)。无则 None。"""
    if isinstance(node, dict):
        for k, v in node.items():
            r = _find_value_path(v, value, f"{prefix}.{k}" if prefix else k)
            if r is not None:
                return r
    elif isinstance(node, list):
        for i, v in enumerate(node):
            r = _find_value_path(v, value, f"{prefix}[{i}]")
            if r is not None:
                return r
    elif node is not None and not isinstance(node, bool) and str(node) == str(value):
        return prefix or None
    return None


def discover_step_links(writes: list[dict]) -> list[dict]:
    """有序写请求(含 response_json)→ 步间数据流(Q3):某步 body 的值 == 更早某步「响应」里的值 → step: 链。

    返回 [{target_step, target_path, source_step, source_path}]。如第2步 flowTask.taskId 来自第1步响应 data.taskId。
    只认 ≥4 长的值,避免 0/1/短码误连。通用,不挑系统。
    """
    bodies = [_parse_body(w.get("post_data")) for w in writes]
    links: list[dict] = []
    for i, body in enumerate(bodies):
        if body is None:
            continue
        for tpath, tval, _raw in _leaf_paths(body):
            if len(tval) < 4:
                continue
            for j in range(i):
                resp = writes[j].get("response_json")
                if resp is None:
                    continue
                sp = _find_value_path(resp, tval)
                if sp is not None:
                    links.append({"target_step": i, "target_path": tpath,
                                  "source_step": j, "source_path": sp})
                    break
    return links


_NAME_LIKE = ("nickname", "name", "realname", "username", "label", "title", "text", "fullname", "dept")


def _pick_label_key(item: dict, value_key: str) -> str:
    """从列表项里挑"像名字"的字段当 label(nickName/name/realName…);没有就用 value_key。"""
    for k in item:
        if k != value_key and any(n in k.lower() for n in _NAME_LIKE) and item[k] not in (None, ""):
            return k
    return value_key


_IDLIKE = _re.compile(r"(id|code|key|value|no|num)$", _re.I)


def _is_idlike(key: str) -> bool:
    """命中的列表字段是不是"ID 类"(select 引用的是项的 ID,不是某段文本)。"""
    return bool(key) and bool(_IDLIKE.search(key))


def suggest_selects(post_data: str | None, reads: list[dict]) -> list[dict]:
    """提交体里"等于某候选列表项 ID 的值"的字段 → 绑 select(Q2 选领导:Agent 传名字→查 ID)。

    防误报(真实表单上 't'/'1' 这类短值会碰巧命中大字典):① 跳过长度 <2 的值;② 命中的列表字段必须
    是 ID 类(value_key 像 id/code/value…);③ 一个源命中 >3 个不同字段 = 太泛(通用字典),整源丢弃。
    """
    body = _parse_body(post_data)
    if body is None:
        return []
    leaves = [(p, sv) for p, sv, _ in _leaf_paths(body) if len(sv) >= 2]   # 跳过 t/1 等过短值
    out: list[dict] = []
    seen: set[str] = set()
    for r in reads:
        items = as_list_payload(r.get("json"))
        if not items or not isinstance(items[0], dict):
            continue
        hits: list[dict] = []
        for path, sv in leaves:
            if path in seen:
                continue
            for it in items:
                vk = next((k for k, v in it.items() if str(v) == sv and _is_idlike(k)), None)
                if vk is None:
                    continue
                lk = _pick_label_key(it, vk)
                hits.append({"path": path, "value": sv, "source_url": r.get("url"),
                             "value_key": vk, "label_key": lk,
                             "label": str(it.get(lk, "")), "count": len(items)})
                break
        if len(hits) > 3:                 # 一个源命中 >3 个不同字段 = 通用字典误命中,整源丢弃
            continue
        for h in hits:
            if h["path"] in seen:
                continue
            seen.add(h["path"])
            out.append(h)
    return out


def _storage_scalars(storage_state: dict | None) -> dict:
    """登录态里所有离散标量值(localStorage 值递归解析 JSON + cookie 值)→ {值: 来源路径}。

    用于把提交字段认成"当前用户 / 会话值"(applicantId 等)→ 运行期从会话重取,不冻结(修 Q1 坑)。
    """
    out: dict[str, str] = {}
    if not storage_state:
        return out

    def add(v, src):
        if isinstance(v, (str, int)) and str(v) not in ("", "0", "1", "true", "false", "null"):
            out.setdefault(str(v), src)

    def walk(node, src):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, f"{src}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{src}[{i}]")
        else:
            add(node, src)

    for o in storage_state.get("origins") or []:
        for it in o.get("localStorage") or []:
            name, val = it.get("name"), it.get("value", "")
            try:
                walk(json.loads(val), f"localStorage:{name}")
            except Exception:  # noqa: BLE001 —— 非 JSON 字符串值,整存
                add(val, f"localStorage:{name}")
    for c in storage_state.get("cookies") or []:
        add(c.get("value"), f"cookie:{c.get('name')}")
    return out


def suggest_identity(post_data: str | None, storage_state: dict | None) -> list[dict]:
    """提交体里"等于登录态里某值"的字段(如 applicantId=当前用户)→ 建议标 identity(运行期重取,不冻结)。"""
    body = _parse_body(post_data)
    if body is None:
        return []
    scal = _storage_scalars(storage_state)
    out: list[dict] = []
    for path, sv, _raw in _leaf_paths(body):
        if sv and sv in scal:
            out.append({"path": path, "value": sv, "source": scal[sv]})
    return out


def list_read_requests(reads: list[dict]) -> list[dict]:
    """从抓到的读响应里挑出「列表型」候选(select 候选源),给出条数 + 列表项字段名。

    供 P3 让用户把某个提交字段(如 approverId)绑定到「来自哪个列表 + 哪个字段是名字/哪个是值」。
    """
    out: list[dict] = []
    for r in reads:
        items = as_list_payload(r.get("json"))
        if not items:
            continue
        first = items[0] if isinstance(items[0], dict) else {}
        out.append({"url": r.get("url"), "count": len(items),
                    "item_keys": list(first.keys())[:20]})
    return out


def pick_submit_request(requests: list[dict], samples: dict) -> dict | None:
    """从抓到的请求里挑"提交请求":写方法 + JSON body + 含最多用户填的值。都不含则取最后一个写请求。"""
    sample_vals = {str(v) for v in samples.values() if v not in ("", None)}
    best, best_score, last_write = None, -1, None
    for r in requests:
        if (r.get("method") or "").upper() not in _WRITE:
            continue
        url = r.get("url") or ""
        if any(n in url for n in _NOISE):
            continue
        body = _parse_body(r.get("post_data"))
        if body is None:
            continue
        last_write = r
        vals = set(_values(body))
        score = len(sample_vals & vals)               # body 里命中几个用户填的值
        if score > best_score:
            best, best_score = r, score
    return best if (best is not None and best_score > 0) else last_write


def parameterize_request(req: dict, samples: dict, base_url: str = "") -> dict | None:
    """把请求体里"等于用户样例值"的字段替换成 {{字段}} 占位;内部 ID/常量保持原样。

    返回 {method, path, body_template(占位后的JSON), params:[字段], sample_inputs, content_type}。
    """
    body = _parse_body(req.get("post_data"))
    if body is None:
        return None
    val2field = {str(v): k for k, v in samples.items() if v not in ("", None)}
    params: dict[str, str] = {}

    def walk(node):
        if isinstance(node, dict):
            return {k: walk(v) for k, v in node.items()}
        if isinstance(node, list):
            return [walk(x) for x in node]
        sv = str(node)
        if sv in val2field:                            # 这个值是用户填的 → 变参数
            f = val2field[sv]
            params[f] = sv
            return "{{" + f + "}}"
        return node                                    # 内部 ID/常量 → 原样保留

    templ = walk(body)
    url = req.get("url") or ""
    path = url
    if base_url and url.startswith(base_url):
        path = url[len(base_url):] or "/"
    elif url.startswith("http"):                       # 去掉协议+域名,留 path(+query)
        from urllib.parse import urlparse
        u = urlparse(url)
        path = (u.path or "/") + (("?" + u.query) if u.query else "")
    return {"method": (req.get("method") or "POST").upper(), "path": path, "url": url,
            "content_type": req.get("content_type", "application/json"),
            "body_template": templ, "params": list(params.keys()),
            "sample_inputs": params, "auth_headers": extract_auth_headers(req.get("headers"))}


# key 像内部标识(默认不当参数):以 id/key/code/token/... 结尾
_ID_KEY = _re.compile(r"(id|key|code|token|uuid|guid|seq|no|flag|status)$", _re.I)
# key 像日期/时间(即便值是 13 位毫秒时间戳,也该当参数,不能被"长数字"规则误判成常量)
_TIME_KEY = _re.compile(r"(time|date|day|start|end|begin|expire|deadline|datetime)", _re.I)


def _is_const_value(v) -> bool:
    """像内部常量的值(默认不建议作参数):bool/null、长 hex、雪花 id(≥16 位数字)、uuid、
    snake_case 标识(如 oa_duty_leave —— 表单类型/流程键,几乎一定是固定值)。"""
    if isinstance(v, bool) or v is None:
        return True
    s = str(v)
    return bool(_re.fullmatch(r"[0-9a-fA-F]{16,}", s)          # 长 hex
               or _re.fullmatch(r"-?\d{16,}", s)               # 雪花 id(≥16 位;13 位毫秒时间戳不算)
               or _re.fullmatch(r"[0-9a-fA-F-]{32,}", s)       # uuid 形态
               or _re.fullmatch(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)+", s))  # snake_case 标识(oa_duty_leave)


def _date_keys(s) -> set:
    """从一个值里抽出 YYYY-MM-DD(支持 ISO 串 / 12-13 位毫秒时间戳),用于日期字段跨格式匹配。"""
    out: set = set()
    s = str(s)
    m = _re.search(r"\d{4}-\d{2}-\d{2}", s)
    if m:
        out.add(m.group(0))
    elif s.isdigit() and 12 <= len(s) <= 13:
        try:
            ms = int(s) if len(s) == 13 else int(s) * 1000
            for off in (8, 0):                                  # 优先东八区(中国 OA),再 UTC
                out.add(_dt.datetime.fromtimestamp(ms / 1000 + off * 3600, _dt.timezone.utc).strftime("%Y-%m-%d"))
        except Exception:  # noqa: BLE001
            pass
    return out


def flatten_body(post_data: str | None, samples: dict | None = None) -> list[dict]:
    """把请求体拍平成叶子字段列表 + 参数建议,供前端勾选。任意嵌套(dict/list)→ 点路径。

    suggest_name=字段中文名(录制时的 DOM 标签),对不上时退回原始 key。文本按值直接对;**日期跨格式对**
    (请求体毫秒戳 ↔ 表单显示的 2026-06-24)。下拉的代码值(2↔事假)无法按值对,退 key。
    """
    body = _parse_body(post_data)
    if body is None:
        return []
    samples = samples or {}
    val2field = {str(v): k for k, v in samples.items() if v not in ("", None)}
    date2field: dict[str, str] = {}                             # 日期(YYYY-MM-DD)→ 中文标签
    for disp, field in val2field.items():
        for dk in _date_keys(disp):
            date2field.setdefault(dk, field)
    out: list[dict] = []

    used: set = set()

    def walk(node, path):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, f"{path}.{k}" if path else k)
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")
        else:
            sv = "" if node is None else str(node)
            key = path.split(".")[-1].split("[")[0]
            label = val2field.get(sv)                           # 文本直接对(原因…)
            if label is None:                                   # 日期跨格式对(毫秒戳 ↔ 显示日期)
                for dk in _date_keys(sv):
                    if dk in date2field:
                        label = date2field[dk]
                        break
            if label is not None:
                used.add(label)
            time_like = bool(_TIME_KEY.search(key))
            const = (not time_like) and (bool(_ID_KEY.search(key)) or _is_const_value(node))
            out.append({"path": path, "key": key, "value": sv,
                        "suggest_param": bool(label is not None or (not const and sv != "")),
                        "suggest_name": label or key, "_const": const, "_matched": label is not None})

    walk(body, "")
    # 顺序兜底:剩下没按值/日期对上的 DOM 标签(按录制顺序),补给还没拿到中文名的"变化字段"(如下拉 type↔请假类型)
    rem = [lab for lab in samples if lab not in used]
    i = 0
    for e in out:
        if not e["_matched"] and not e["_const"] and e["suggest_param"] and e["suggest_name"] == e["key"]:
            if i < len(rem):
                e["suggest_name"] = rem[i]
                i += 1
    for e in out:
        e.pop("_const", None)
        e.pop("_matched", None)
    return out


def build_api_request(req: dict, param_map: dict, base_url: str = "",
                      selects: list[dict] | None = None, identity: list[dict] | None = None) -> dict | None:
    """param_map: {字段点路径 → 参数名}。把这些路径的叶子替换成 {{参数名}},其余原样。

    selects:[{path, source_url, value_key, label_key}](Q2 选领导,path 须在 param_map 里 → 运行期名字→ID);
    identity:[{path, source}](Q1 当前用户/会话值,运行期重取覆盖,不作参数)。
    返回 {method, path, url, content_type, body_template, params, sample_inputs, auth_headers, selects, identity}。
    """
    body = _parse_body(req.get("post_data"))
    if body is None:
        return None
    params: list[str] = []
    samples: dict[str, str] = {}

    def walk(node, path):
        if isinstance(node, dict):
            return {k: walk(v, f"{path}.{k}" if path else k) for k, v in node.items()}
        if isinstance(node, list):
            return [walk(v, f"{path}[{i}]") for i, v in enumerate(node)]
        if path in param_map:
            name = param_map[path]
            params.append(name)
            samples[name] = "" if node is None else str(node)
            return "{{" + name + "}}"
        return node

    templ = walk(body, "")
    url = req.get("url") or ""
    path = url
    if base_url and url.startswith(base_url):
        path = url[len(base_url):] or "/"
    elif url.startswith("http"):
        from urllib.parse import urlparse
        u = urlparse(url)
        path = (u.path or "/") + (("?" + u.query) if u.query else "")
    # select 元数据:path 须是参数(在 param_map),记成 param→源/键,运行期按名字查 ID
    sel_meta = [{"param": param_map[s["path"]], "source_url": s.get("source_url"),
                 "value_key": s.get("value_key"), "label_key": s.get("label_key")}
                for s in (selects or []) if s.get("path") in param_map]
    id_meta = [{"path": i["path"], "source": i.get("source", "")} for i in (identity or [])]
    return {"method": (req.get("method") or "POST").upper(), "path": path, "url": url,
            "content_type": req.get("content_type", "application/json"),
            "body_template": templ, "params": list(dict.fromkeys(params)), "sample_inputs": samples,
            "auth_headers": extract_auth_headers(req.get("headers")),
            "selects": sel_meta, "identity": id_meta}


def substitute(template, fields: dict, defaults: dict | None = None):
    """把 body_template 里的 {{字段}} 占位填回。优先用运行期 fields;没传该字段则退回 defaults(录制时的原值)
    → "全选"也安全:agent 没改的字段保持录制值(固定字段不变),不会留下空占位。"""
    defaults = defaults or {}
    if isinstance(template, dict):
        return {k: substitute(v, fields, defaults) for k, v in template.items()}
    if isinstance(template, list):
        return [substitute(x, fields, defaults) for x in template]
    if isinstance(template, str) and template.startswith("{{") and template.endswith("}}"):
        key = template[2:-2]
        if key in fields:
            return fields[key]
        return defaults.get(key, template)
    return template


# ─────────── P4:select 名字→ID / identity 运行期重取 ───────────
def _split_path(path: str) -> list:
    """'form.items[0].id' → ['form','items',0,'id'](点路径 + 数组下标)。"""
    out: list = []
    for seg in path.split("."):
        bits = seg.split("[")
        if bits[0]:
            out.append(bits[0])
        for idx in bits[1:]:
            out.append(int(idx.rstrip("]")))
    return out


def _get_by_path(node, path: str):
    for k in _split_path(path):
        try:
            node = node[k]
        except Exception:  # noqa: BLE001
            return None
    return node


def _set_by_path(node, path: str, value) -> None:
    ks = _split_path(path)
    for k in ks[:-1]:
        try:
            node = node[k]
        except Exception:  # noqa: BLE001
            return
    try:
        node[ks[-1]] = value
    except Exception:  # noqa: BLE001
        pass


def resolve_identity_value(source: str, storage_state: dict | None):
    """从登录态按 source 取"当前用户/会话值"。source 形如 localStorage:userInfo.userId / cookie:JSESSIONID。"""
    if not storage_state or not source:
        return None
    kind, _, rest = source.partition(":")
    if kind == "cookie":
        for c in storage_state.get("cookies") or []:
            if c.get("name") == rest:
                return c.get("value")
        return None
    if kind == "localStorage":
        name, _, path = rest.partition(".")
        for o in storage_state.get("origins") or []:
            for it in o.get("localStorage") or []:
                if it.get("name") == name:
                    val = it.get("value", "")
                    if not path:
                        return val
                    try:
                        return _get_by_path(json.loads(val), path)
                    except Exception:  # noqa: BLE001
                        return val
    return None


def _apply_identity(body, api_request: dict, storage_state: dict | None) -> None:
    """把 identity 字段在运行期用会话里的当前用户值覆盖(不冻结成录制者)。"""
    for idn in api_request.get("identity") or []:
        val = resolve_identity_value(idn.get("source", ""), storage_state)
        if val is not None:
            _set_by_path(body, idn.get("path", ""), val)


async def _fetch_list(url: str, base_url: str, storage_state, token_key: str, verify: bool,
                      auth_headers: dict | None) -> list:
    """带登录态 GET 一个候选列表(选领导源),用 as_list_payload 取出数组。失败返回 []。"""
    full = url if url.startswith("http") else (base_url or "").rstrip("/") + url
    from urllib.parse import urlparse
    host = urlparse(full).hostname or ""
    headers: dict = dict(auth_headers or {})
    ck = _auth_headers(storage_state, host, token_key)
    if ck.get("Cookie"):
        headers["Cookie"] = ck["Cookie"]
    if "Authorization" not in headers and not (auth_headers or {}) and ck.get("Authorization"):
        headers["Authorization"] = ck["Authorization"]
    import httpx
    try:
        async with httpx.AsyncClient(timeout=30, verify=verify) as c:
            r = await c.get(full, headers=headers)
        return as_list_payload(r.json()) or []
    except Exception:  # noqa: BLE001
        return []


async def _resolve_selects(api_request: dict, fields: dict, *, base_url: str, storage_state,
                           token_key: str, verify: bool) -> dict:
    """Q2 选领导:参数传的是名字 → 查候选列表把它换成内部 ID。查不到则原样(可能用户直接给了 ID)。"""
    for s in api_request.get("selects") or []:
        param = s.get("param")
        if param not in fields:
            continue
        name = fields[param]
        items = await _fetch_list(s.get("source_url", ""), base_url, storage_state, token_key, verify,
                                  api_request.get("auth_headers"))
        lk, vk = s.get("label_key"), s.get("value_key")
        match = next((it for it in items if isinstance(it, dict) and str(it.get(lk)) == str(name)), None)
        if match is not None and vk in match:
            fields[param] = match[vk]
    return fields


def _auth_headers(storage_state: dict | None, host: str, token_key: str = "Admin-Token") -> dict:
    """从登录态快照构造鉴权头:同域 cookie 全带上 + token(cookie/localStorage)→ Authorization Bearer。"""
    headers: dict[str, str] = {}
    if not storage_state:
        return headers
    pairs: list[str] = []
    tok = ""
    for c in storage_state.get("cookies") or []:
        cd = (c.get("domain") or "").lstrip(".")
        if host and cd and cd not in host and host not in cd:
            continue
        pairs.append(f"{c.get('name')}={c.get('value')}")
        if c.get("name") == token_key:
            tok = c.get("value", "")
    if not tok:
        for o in storage_state.get("origins") or []:
            for it in o.get("localStorage") or []:
                if it.get("name") == token_key:
                    tok = it.get("value", "")
    if pairs:
        headers["Cookie"] = "; ".join(pairs)
    if tok:
        headers["Authorization"] = "Bearer " + tok
    return headers


async def execute_api_request(api_request: dict, fields: dict, *, base_url: str = "",
                              storage_state: dict | None = None, send: bool = True,
                              verify: bool = True, token_key: str = "Admin-Token",
                              overrides: dict | None = None) -> dict:
    """参数填回 body_template,带登录态发请求(send=True)或只校验参数齐全(send=False,dry,写安全)。

    P4:发真请求前 ① select 把参数里的名字换成内部 ID(选领导);② substitute 后用会话里的当前用户值
    覆盖 identity 字段(申请人=谁调用就是谁,不冻结成录制者);③ overrides 把上一步响应值注入本步 body
    (Q3 步链,如 taskId)。dry 不连网,只校验参数齐全。
    """
    fields = dict(fields)
    if send:                                                 # 选领导:名字→ID(需连网查候选列表)
        fields = await _resolve_selects(api_request, fields, base_url=base_url,
                                        storage_state=storage_state, token_key=token_key, verify=verify)
    body = substitute(api_request.get("body_template"), fields, api_request.get("sample_inputs") or {})
    _apply_identity(body, api_request, storage_state)        # 当前用户/会话值运行期重取覆盖
    for p, v in (overrides or {}).items():                   # Q3:上一步响应值注入(taskId 等)
        _set_by_path(body, p, v)
    method = (api_request.get("method") or "POST").upper()
    path = api_request.get("path") or ""
    # 优先用录制时的完整 url(同一 OA host 不变);否则 base_url + path
    url = api_request.get("url") or (path if path.startswith("http") else (base_url or "").rstrip("/") + path)
    if not send:
        leftover = "{{" in json.dumps(body, ensure_ascii=False)   # 还有没填上的 {{字段}}?
        return {"ok": not leftover, "dry": True, "method": method, "url": url, "body": body,
                "detail": "有参数没填上" if leftover else "请求可构造(dry,未真发)"}
    from urllib.parse import urlparse
    host = urlparse(url).hostname or ""
    headers = {"Content-Type": api_request.get("content_type") or "application/json"}
    # ① 录制时抓到的应用自定义鉴权头(Authorization / Admin-Token / satoken / 租户号…)原样带上 —— 通用,不挑系统
    headers.update(api_request.get("auth_headers") or {})
    # ② Cookie 用 storageState 的(更全/可能更新);没抓到自定义头时,才回退到按 token_key 猜 Authorization
    ck = _auth_headers(storage_state, host, token_key)
    if ck.get("Cookie"):
        headers["Cookie"] = ck["Cookie"]
    if "Authorization" not in headers and not (api_request.get("auth_headers") or {}) and ck.get("Authorization"):
        headers["Authorization"] = ck["Authorization"]
    import httpx
    async with httpx.AsyncClient(timeout=30, verify=verify) as c:
        r = await c.request(method, url, json=body, headers=headers)
    try:
        data = r.json()
    except Exception:  # noqa: BLE001
        data = {"raw": r.text[:1000]}
    return {"ok": 200 <= r.status_code < 300, "status": r.status_code, "response": data,
            "method": method, "url": url}


async def execute_api_workflow(workflow: dict, fields: dict, *, base_url: str = "",
                               storage_state: dict | None = None, send: bool = True,
                               verify: bool = True, token_key: str = "Admin-Token") -> dict:
    """Q3 多写步链:按 steps 顺序执行(每步=录到的一个请求);step.links 把更早步「响应」里的值注入本步 body
    (如 taskId)。每步带各自 select/identity。任一步失败整体失败;最终步即业务结果。
    """
    steps = workflow.get("steps") or []
    responses: list = []
    last: dict = {}
    for i, step in enumerate(steps):
        overrides: dict = {}
        for lk in step.get("links") or []:
            src = responses[lk["source_step"]] if 0 <= lk.get("source_step", -1) < len(responses) else None
            if src is not None:
                val = _get_by_path(src, lk.get("source_path", ""))
                if val is not None:
                    overrides[lk.get("target_path", "")] = val
        out = await execute_api_request(step, fields, base_url=base_url, storage_state=storage_state,
                                        send=send, verify=verify, token_key=token_key, overrides=overrides)
        last = out
        responses.append(out.get("response") if send else out.get("body"))
        if not out.get("ok"):
            return {"ok": False, "failed_step": i, "detail": f"第{i + 1}步失败", "step_result": out}
    return {"ok": bool(last.get("ok", True)), "steps": len(steps),
            "status": last.get("status"), "response": last.get("response"), "final": last}


async def execute_api(api_request: dict, fields: dict, **kw) -> dict:
    """统一入口:api_request 有 steps → 多步工作流(Q3);否则单请求。调用方不必关心是几步。"""
    if api_request.get("steps"):
        return await execute_api_workflow(api_request, fields, **kw)
    return await execute_api_request(api_request, fields, **kw)


def build_api_workflow(writes: list[dict], *, param_map: dict, base_url: str = "",
                       selects: list[dict] | None = None, identity: list[dict] | None = None) -> dict:
    """把有序写请求组装成多步工作流(Q3):每步=一个抓到的请求;**最后一步**带用户参数/select/identity,
    其余步是常量(其动态值靠步链注入);自动发现步间数据流(taskId 等)挂到对应目标步。

    返回 {steps:[...]}(放进 PageScriptBody.api_request;运行期 execute_api 自动走工作流)。
    """
    n = len(writes)
    steps: list[dict] = []
    for i, w in enumerate(writes):
        last = i == n - 1
        apir = build_api_request(w, param_map if last else {}, base_url,
                                 selects=selects if last else None,
                                 identity=identity if last else None)
        steps.append(apir or {})
    for lk in discover_step_links(writes):                   # 步间数据流挂到目标步
        steps[lk["target_step"]].setdefault("links", []).append(
            {"target_path": lk["target_path"], "source_step": lk["source_step"],
             "source_path": lk["source_path"]})
    return {"steps": steps}
